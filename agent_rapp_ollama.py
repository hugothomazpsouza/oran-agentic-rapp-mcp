import asyncio
import json
import sys
import time

import ollama
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL_NAME = "qwen2.5:3b"

SYSTEM_PROMPT = """
Voce e um rApp agentico no Non-RT RIC (O-RAN). Traduza a intencao do operador em politicas A1 validas.
Siga esta ordem obrigatoriamente, um passo de cada vez:
1. Chame list_near_rt_rics para verificar os RICs e policytype_ids disponiveis.
2. Chame get_policy_type_schema para obter o JSON Schema do policytype_id.
3. Chame apply_a1_policy enviando policy_data exatamente no formato do schema.
4. Chame list_active_policies para confirmar e responda de forma curta o que foi feito.
"""


async def run_agent(intent: str):
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["nonrtric_mcp_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Descobre ferramentas MCP e converte para o formato Ollama
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

            print(f"\n[MCP] Ferramentas descobertas: {[t.name for t in mcp_tools.tools]}")
            print(f"[MODELO LOCAL] {MODEL_NAME}")
            print(f"[INTENT] {intent}\n" + "-" * 60)

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": intent},
            ]

            t_total = time.perf_counter()

            for step in range(1, 10):
                t_step = time.perf_counter()
                response = ollama.chat(
                    model=MODEL_NAME,
                    messages=messages,
                    tools=ollama_tools,
                    options={"temperature": 0.0},
                )
                llm_elapsed = time.perf_counter() - t_step

                msg = response.message
                messages.append(msg)

                if not msg.tool_calls:
                    total_elapsed = time.perf_counter() - t_total
                    print(
                        f"\n[rApp FINAL ({MODEL_NAME}) - Passo {step} | "
                        f"LLM: {llm_elapsed:.2f}s | Total: {total_elapsed:.2f}s]\n"
                        f"{msg.content}"
                    )
                    break

                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = tc.function.arguments or {}
                    print(
                        f"[Passo {step} (LLM {llm_elapsed:.2f}s) -> MCP] "
                        f"{fn_name}({json.dumps(fn_args)})"
                    )

                    t_mcp = time.perf_counter()
                    mcp_result = await session.call_tool(fn_name, arguments=fn_args)
                    mcp_elapsed = time.perf_counter() - t_mcp

                    result_text = "\n".join(
                        c.text for c in mcp_result.content if hasattr(c, "text")
                    )
                    print(f"[Passo {step} <- MCP ({mcp_elapsed:.3f}s)] {result_text}\n")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": fn_name,
                            "content": result_text,
                        }
                    )


if __name__ == "__main__":
    user_intent = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else (
            "Verifique os RICs disponiveis e aplique uma politica de QoS "
            "com policy_id 'pol-local-01' no ric1 para o UE 'ue-002' "
            "com qosId 'slice-embb' e priorityLevel 15."
        )
    )
    asyncio.run(run_agent(user_intent))
