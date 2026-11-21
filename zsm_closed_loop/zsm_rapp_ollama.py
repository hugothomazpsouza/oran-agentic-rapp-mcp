import asyncio
import json
import os
import sys
import time
import requests
import ollama

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL_NAME = "qwen2.5:3b"
KPI_URL = "http://localhost:8090/kpis"
CHECK_INTERVAL_SEC = 10
MCP_SERVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zsm_mcp_server.py")

SYSTEM_PROMPT = """
Voce e um rApp agentico ZSM no Non-RT RIC (O-RAN) acionado por violacao de SLA.
Siga exatamente esta ordem, um passo de cada vez:
1. Chame get_cell_kpis para confirmar qual celula/UE/slice violou o SLA de latencia (> 10 ms).
2. Chame list_near_rt_rics para verificar o ric_id e o policytype_id suportado.
3. Chame get_policy_type_schema para o policytype_id retornado.
4. Chame apply_a1_policy com policy_id 'pol-zsm-ollama' definindo priorityLevel igual a 25 para o UE e slice afetados, seguindo estritamente o schema.
5. Chame get_cell_kpis novamente para confirmar que a latencia caiu para <= 10 ms e entregue um resumo curto da causa raiz e da remediacao.
"""


async def remediate_with_ollama(session, ollama_tools):
    t_total = time.perf_counter()
    alert_prompt = (
        "ALERTA ZSM: degradacao de SLA detectada na RAN. "
        "Diagnostique via get_cell_kpis, aplique a politica A1 corretiva no ric1 "
        "e valide se a latencia voltou para <= 10 ms."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": alert_prompt},
    ]

    for step in range(1, 10):
        t_llm = time.perf_counter()
        response = ollama.chat(
            model=MODEL_NAME,
            messages=messages,
            tools=ollama_tools,
            options={"temperature": 0.0},
        )
        llm_sec = time.perf_counter() - t_llm

        msg = response.message
        messages.append(msg)

        if not msg.tool_calls:
            total_sec = time.perf_counter() - t_total
            print(
                f"\n[ZSM CONCLUIDO ({MODEL_NAME}) | Passos: {step} | Tempo total: {total_sec:.2f}s]\n"
                f"{msg.content}\n" + "=" * 65
            )
            return

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = tc.function.arguments or {}
            print(f"  [Passo {step} (LLM {llm_sec:.2f}s) -> MCP] {fn_name}({json.dumps(fn_args)})")

            t_mcp = time.perf_counter()
            mcp_result = await session.call_tool(fn_name, arguments=fn_args)
            mcp_sec = time.perf_counter() - t_mcp

            result_text = "\n".join(
                c.text for c in mcp_result.content if hasattr(c, "text")
            )
            print(f"  [Passo {step} <- MCP ({mcp_sec:.3f}s)] {result_text[:200]}{'...' if len(result_text) > 200 else ''}")

            messages.append(
                {
                    "role": "tool",
                    "tool_name": fn_name,
                    "content": result_text,
                }
            )


async def main():
    server_params = StdioServerParameters(command=sys.executable, args=[MCP_SERVER_PATH])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()

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

            print(f"[ZSM-OLLAMA ({MODEL_NAME})] Conectado ao MCP | Tools: {[t.name for t in mcp_tools.tools]}")
            print(f"[ZSM-OLLAMA] Monitorando {KPI_URL} a cada {CHECK_INTERVAL_SEC}s (Ctrl+C para sair)\n")

            while True:
                try:
                    data = requests.get(KPI_URL, timeout=3).json()
                except Exception as e:
                    print(f"[ERRO] Simulador de KPIs inacessivel em {KPI_URL}: {e}")
                    await asyncio.sleep(CHECK_INTERVAL_SEC)
                    continue

                k1 = next(c for c in data["cells"] if c["cell_id"] == "kista_1")
                ts = time.strftime("%H:%M:%S")

                if k1["latency_ms"] <= 10.0:
                    print(
                        f"[{ts}] [MONITOR OK] kista_1 | PRB: {k1['prb_utilization_pct']}% | "
                        f"Latencia: {k1['latency_ms']} ms | Prio A1: {k1['active_a1_priority']} | "
                        f"Status: {k1['status']} (LLM standby)"
                    )
                else:
                    print("\n" + "=" * 65)
                    print(
                        f"[{ts}] [ANOMALIA SLA] kista_1 | Latencia: {k1['latency_ms']} ms (> 10 ms) | "
                        f"PRB: {k1['prb_utilization_pct']}%"
                    )
                    print(f"[ZSM-OLLAMA] Acionando {MODEL_NAME} para RCA e remediacao...")
                    await remediate_with_ollama(session, ollama_tools)

                await asyncio.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
