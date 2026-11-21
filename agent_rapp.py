import asyncio
import json
import os
import sys
import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Ordem do mais leve/economico para fallback caso algum esteja em 503
MODEL_FALLBACKS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]

SYSTEM_PROMPT = """
Voce e um rApp agentico no Non-RT RIC (O-RAN). Traduza a intencao em politicas A1 validas.
Regras:
1. Liste os RICs antes de aplicar qualquer politica.
2. Consulte get_policy_type_schema antes de chamar apply_a1_policy.
3. Siga estritamente o schema retornado.
4. Apos aplicar, liste as politicas ativas e responda de forma breve o que foi alterado.
"""


def call_llm_with_fallback(client, contents, tools_config):
    last_error = None
    for model_name in MODEL_FALLBACKS:
        for attempt in range(1, 3):
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
                return model_name, resp
            except ServerError as e:
                last_error = e
                print(f"[AVISO] {model_name} indisponivel (503) tentativa {attempt}/2...")
                time.sleep(3)
            except ClientError as e:
                last_error = e
                print(f"[AVISO] {model_name} rejeitado ({e.code}), pulando para o proximo...")
                break
    raise last_error


async def run_agent(intent: str):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERRO: defina GEMINI_API_KEY antes de rodar.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["nonrtric_mcp_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description or "",
                    parameters_json_schema=t.inputSchema,
                )
                for t in mcp_tools.tools
            ]
            tools_config = [types.Tool(function_declarations=declarations)]

            print(f"\n[MCP] Ferramentas descobertas: {[t.name for t in mcp_tools.tools]}")
            print(f"[INTENT] {intent}\n" + "-" * 60)

            contents = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=intent)],
                )
            ]

            for step in range(1, 10):
                used_model, response = call_llm_with_fallback(
                    client, contents, tools_config
                )

                candidate_content = response.candidates[0].content
                contents.append(candidate_content)

                function_calls = [
                    part.function_call
                    for part in candidate_content.parts
                    if part.function_call
                ]

                if not function_calls:
                    print(f"\n[rApp FINAL ({used_model}) - Passo {step}]\n{response.text}")
                    break

                tool_response_parts = []
                for fc in function_calls:
                    args = dict(fc.args) if fc.args else {}
                    print(f"[Passo {step} ({used_model}) -> MCP] {fc.name}({json.dumps(args)})")

                    mcp_result = await session.call_tool(fc.name, arguments=args)
                    result_text = "\n".join(
                        c.text for c in mcp_result.content if hasattr(c, "text")
                    )
                    print(f"[Passo {step} <- MCP] {result_text}\n")

                    try:
                        parsed = json.loads(result_text)
                        response_dict = (
                            parsed if isinstance(parsed, dict) else {"result": parsed}
                        )
                    except json.JSONDecodeError:
                        response_dict = {"result": result_text}

                    tool_response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response=response_dict,
                        )
                    )

                contents.append(
                    types.Content(role="user", parts=tool_response_parts)
                )


if __name__ == "__main__":
    user_intent = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else (
            "Verifique os RICs disponiveis e aplique uma politica de QoS "
            "com policy_id 'pol-urllc-01' no ric1 para o UE 'ue-001' "
            "com qosId 'slice-urllc' e priorityLevel 10."
        )
    )
    asyncio.run(run_agent(user_intent))
