import argparse
import asyncio
import json
import os
import sys
import time
import requests

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

KPI_URL = "http://localhost:8090/kpis"
MCP_SERVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zsm_mcp_server.py")

MODEL_FALLBACKS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]

SYSTEM_PROMPT = """
Voce e um rApp agentico ZSM (Zero-touch Service Management) no Non-RT RIC (O-RAN).
Voce foi acionado porque uma anomalia de SLA foi detectada na RAN.

Regras operacionais obrigatorias:
1. Chame get_cell_kpis para confirmar qual celula/UE/slice violou o SLA de latencia (< 10 ms).
2. Chame list_near_rt_rics e get_policy_type_schema para o policytype_id suportado.
3. Para mitigar congestionamento em slice URLLC, aplique uma politica A1 com policy_id 'pol-zsm-urllc-auto' definindo priorityLevel igual a 25 para o UE e slice afetados.
4. Apos aplicar, chame get_cell_kpis novamente para validar se o SLA foi restaurado (< 10 ms) e entregue um resumo curto da causa raiz e da acao tomada.
"""


async def run_gemini_remediation(session, mcp_tools, alert_prompt: str):
    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError, ServerError

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Defina GEMINI_API_KEY ou rode com --provider ollama.")

    client = genai.Client(api_key=api_key)
    declarations = [
        types.FunctionDeclaration(
            name=t.name,
            description=t.description or "",
            parameters_json_schema=t.inputSchema,
        )
        for t in mcp_tools.tools
    ]
    tools_config = [types.Tool(function_declarations=declarations)]
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=alert_prompt)])]

    for step in range(1, 10):
        resp, used_model = None, None
        for model_name in MODEL_FALLBACKS:
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=tools_config,
                        temperature=0.1,
                        max_output_tokens=512,
                    ),
                )
                used_model = model_name
                break
            except (ServerError, ClientError) as e:
                print(f"  [AVISO] {model_name} indisponivel ({e}), tentando proximo...")
                time.sleep(2)
        if resp is None:
            raise RuntimeError("Nenhum modelo Gemini disponivel no momento.")

        candidate_content = resp.candidates[0].content
        contents.append(candidate_content)
        fcs = [p.function_call for p in candidate_content.parts if p.function_call]

        if not fcs:
            print(f"\n[ZSM REMEDIACAO CONCLUIDA ({used_model})]\n{resp.text}\n" + "=" * 65)
            return

        parts = []
        for fc in fcs:
            args = dict(fc.args) if fc.args else {}
            print(f"  [Passo {step} -> MCP] {fc.name}({json.dumps(args)})")
            res = await session.call_tool(fc.name, arguments=args)
            txt = "\n".join(c.text for c in res.content if hasattr(c, "text"))
            print(f"  [Passo {step} <- MCP] {txt[:220]}{'...' if len(txt) > 220 else ''}")
            try:
                parsed = json.loads(txt)
                rdict = parsed if isinstance(parsed, dict) else {"result": parsed}
            except json.JSONDecodeError:
                rdict = {"result": txt}
            parts.append(types.Part.from_function_response(name=fc.name, response=rdict))
        contents.append(types.Content(role="user", parts=parts))


async def run_ollama_remediation(session, mcp_tools, alert_prompt: str, model_name: str):
    import ollama

    ollama_tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_tools.tools
    ]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": alert_prompt},
    ]

    for step in range(1, 10):
        t0 = time.perf_counter()
        response = ollama.chat(
            model=model_name,
            messages=messages,
            tools=ollama_tools,
            options={"temperature": 0.0},
        )
        elapsed = time.perf_counter() - t0
        msg = response.message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"\n[ZSM REMEDIACAO CONCLUIDA ({model_name})]\n{msg.content}\n" + "=" * 65)
            return

        for tc in msg.tool_calls:
            fn, args = tc.function.name, tc.function.arguments or {}
            print(f"  [Passo {step} (LLM {elapsed:.1f}s) -> MCP] {fn}({json.dumps(args)})")
            res = await session.call_tool(fn, arguments=args)
            txt = "\n".join(c.text for c in res.content if hasattr(c, "text"))
            print(f"  [Passo {step} <- MCP] {txt[:220]}{'...' if len(txt) > 220 else ''}")
            messages.append({"role": "tool", "tool_name": fn, "content": txt})


async def zsm_closed_loop(provider: str, ollama_model: str, interval: int):
    server_params = StdioServerParameters(command=sys.executable, args=[MCP_SERVER_PATH])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()
            print(f"[ZSM] Conectado ao MCP ({len(mcp_tools.tools)} tools) | Provider: {provider}")
            print(f"[ZSM] Monitorando {KPI_URL} a cada {interval}s (Ctrl+C para sair)\n")

            while True:
                try:
                    data = requests.get(KPI_URL, timeout=3).json()
                except Exception as e:
                    print(f"[ERRO] Nao foi possivel ler {KPI_URL}: {e}")
                    await asyncio.sleep(interval)
                    continue

                k1 = next(c for c in data["cells"] if c["cell_id"] == "kista_1")
                ts = time.strftime("%H:%M:%S")

                if k1["latency_ms"] <= 10.0:
                    print(
                        f"[{ts}] [MONITOR OK] kista_1 | PRB: {k1['prb_utilization_pct']}% | "
                        f"Latencia: {k1['latency_ms']} ms | Prio A1: {k1['active_a1_priority']} | "
                        f"Status: {k1['status']} (LLM em standby)"
                    )
                else:
                    print("\n" + "=" * 65)
                    print(
                        f"[{ts}] [ANOMALIA DETECTADA] kista_1 violou SLA! "
                        f"Latencia: {k1['latency_ms']} ms (> 10 ms) | PRB: {k1['prb_utilization_pct']}%"
                    )
                    print("[ZSM] Acionando rApp Agentico para RCA e remediacao via A1...")
                    alert = (
                        "ALERTA ZSM: degradacao de SLA detectada na rede RAN. "
                        "Diagnostique via get_cell_kpis, aplique a politica A1 corretiva no ric1 "
                        "e valide se a latencia voltou para abaixo de 10 ms."
                    )
                    if provider == "ollama":
                        await run_ollama_remediation(session, mcp_tools, alert, ollama_model)
                    else:
                        await run_gemini_remediation(session, mcp_tools, alert)

                await asyncio.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Loop Fechado ZSM com rApp Agentico + MCP")
    parser.add_argument("--provider", choices=["gemini", "ollama"], default="gemini")
    parser.add_argument("--ollama-model", default="qwen2.5:3b")
    parser.add_argument("--interval", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(zsm_closed_loop(args.provider, args.ollama_model, args.interval))
