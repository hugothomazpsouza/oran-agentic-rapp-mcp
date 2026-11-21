# Lab: rApp Agêntico com LLM + MCP integrado ao O-RAN Non-RT RIC

Prova de conceito (PoC) de **Intent-Based Networking (IBN)** aplicada à arquitetura **Open RAN (O-RAN)**. Um agente baseado em LLM atua como um **rApp** integrado ao **Non-RT RIC** por meio do **Model Context Protocol (MCP)**, recebendo intenções em linguagem natural e traduzindo-as em políticas enviadas pela interface A1 para aplicação em um **Near-RT RIC** simulado.

O laboratório foi projetado para rodar em ambiente enxuto **sem GPU dedicada** (máquina local ou VM CPU-only no Google Cloud Free Trial), suportando tanto inferência em nuvem (Gemini API) quanto execução 100% local a custo zero (Ollama em CPU).

## Estrutura do Repositório

```text
oran-agentic-rapp-mcp/
├── README.md                  # Documentação, arquitetura e passo a passo do lab
├── requirements.txt           # Dependências Python (com mcp<2 fixado)
├── .gitignore                 # Ignora .venv, __pycache__ e chaves locais
├── nonrtric_mcp_server.py     # Servidor MCP conectado à API REST do Non-RT RIC (PMS)
├── agent_rapp.py              # rApp Agêntico (MCP Client) usando Gemini API (nuvem)
└── agent_rapp_ollama.py       # rApp Agêntico (MCP Client) usando Ollama local (CPU)
```

| Arquivo                  | Papel na Arquitetura          | O que faz                                                                                                                                                                                                                                   |
| :----------------------- | :---------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `nonrtric_mcp_server.py` | **MCP Server**                | Expõe a API REST do Non-RT RIC (`http://localhost:8081/a1-policy/v2`) como 6 ferramentas tipadas via JSON-RPC 2.0 (`stdio`): descoberta de RICs, consulta de JSON Schema, listagem, leitura, criação/atualização e remoção de políticas A1. |
| `agent_rapp.py`          | **rApp Agêntico (Nuvem)**     | Cliente MCP que executa o loop ReAct usando `gemini-3.5-flash-lite` (com fallback automático em caso de `503`) para traduzir intenções em linguagem natural em chamadas MCP válidas.                                                        |
| `agent_rapp_ollama.py`   | **rApp Agêntico (Local CPU)** | Mesmo cliente MCP e loop ReAct, mas rodando 100% offline via Ollama (`qwen2.5:3b`) na CPU, com medição de latência de inferência vs. tempo de execução MCP/A1 por passo.                                                                    |

---

## Documentação Conceitual

Para detalhes sobre o papel de cada componente O-RAN (Non-RT RIC, Near-RT RIC, A1 PMS, rApps, xApps, interfaces A1/E2/O1) e onde cada função fica fisicamente em produção (Torre vs. Edge vs. Cloud), consulte [docs/arquitetura-oran.md](docs/arquitetura-oran.md).

## Arquitetura do Laboratório

```text
+-------------------------------------------------------------------+
|  Operador (Intenção em Linguagem Natural via CLI)                 |
+---------------------------------+---------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|  rApp Agêntico (MCP Client + Loop ReAct)                          |
|  - agent_rapp.py         -> Nuvem (Gemini 3.5 Flash-Lite)         |
|  - agent_rapp_ollama.py  -> Local CPU (Ollama / qwen2.5:3b)       |
+---------------------------------+---------------------------------+
                                  | MCP (JSON-RPC 2.0 via stdio)
                                  v
+-------------------------------------------------------------------+
|  Servidor MCP (nonrtric_mcp_server.py)                            |
|  Tools: list_near_rt_rics, get_policy_type_schema,                |
|         list_active_policies, get_policy_details,                 |
|         apply_a1_policy, delete_a1_policy                         |
+---------------------------------+---------------------------------+
                                  | REST HTTP (:8081/a1-policy/v2)
                                  v
+-------------------------------------------------------------------+
|  O-RAN SC Non-RT RIC — A1 Policy Management Service (PMS)         |
|  Container: policy-agent (nonrtric-a1-policy-management-service)  |
+---------------------------------+---------------------------------+
                                  | Interface A1 (:8085)
                                  v
+-------------------------------------------------------------------+
|  Near-RT RIC Simulado — A1 Simulator (ric1)                       |
|  Container: a1-sim-osc-0 (OSC_2.1.0 | Policy Type 1: QoS_Policy)  |
+-------------------------------------------------------------------+
```

---

## Pré-requisitos

- **SO:** Ubuntu 22.04 ou 24.04 LTS (testado em VM GCP `e2-standard-4`, 4 vCPUs, 16 GB RAM, sem GPU).
- **Ferramentas:** Docker, Docker Compose v2, `git`, `curl`, `jq`, Python 3.12+ (`python3-venv`, `python3-pip`).

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git curl jq python3-venv python3-pip
sudo usermod -aG docker $USER
newgrp docker
```

---

## Passo a Passo da Implementação

### 1. Subir o Non-RT RIC (A1 Policy Management Service)

Clonamos o repositório oficial da O-RAN Software Community e subimos apenas o serviço de políticas A1 (PMS):

```bash
git clone https://github.com/o-ran-sc/nonrtric.git
cd nonrtric/docker-compose
docker compose -f docker-compose.yaml -f policy-service/docker-compose.yaml up -d
```

_O container `policy-agent` fica acessível na porta `8081` (HTTP) e `8433` (HTTPS), conectado à rede Docker `nonrtric-docker-net`._

### 2. Subir o Near-RT RIC Simulado (A1 Simulator)

Conectamos o container do simulador A1 na mesma rede Docker do `policy-agent`:

```bash
NET=$(docker inspect policy-agent -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')

docker run -d --name a1-sim-osc-0 \
  --network "$NET" \
  -p 8085:8085 \
  -e A1_VERSION=OSC_2.1.0 \
  -e ALLOW_HTTP=true \
  nexus3.o-ran-sc.org:10002/o-ran-sc/a1-simulator:2.2.0
```

Validação do simulador:

```bash
curl -s http://localhost:8085/
# Retorno esperado: OK
```

### 3. Cadastrar um Policy Type de QoS no A1 Simulator

Registramos o `policy_type_id = 1` (`QoS_Policy`) com um JSON Schema estrito exigindo `scope` (`ueId`, `qosId`) e `qosObjectives` (`priorityLevel`):

```bash
curl -X PUT "http://localhost:8085/policytype?id=1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "QoS_Policy",
    "description": "Politica de QoS para testes com rApp agentico",
    "policy_type_id": 1,
    "create_schema": {
      "$schema": "[http://json-schema.org/draft-07/schema#](http://json-schema.org/draft-07/schema#)",
      "type": "object",
      "properties": {
        "scope": {
          "type": "object",
          "properties": {
            "ueId": { "type": "string" },
            "qosId": { "type": "string" }
          },
          "required": ["ueId", "qosId"]
        },
        "qosObjectives": {
          "type": "object",
          "properties": {
            "priorityLevel": { "type": "number" }
          },
          "required": ["priorityLevel"]
        }
      },
      "required": ["scope", "qosObjectives"]
    }
  }'
# Retorno esperado: Policy type 1 is OK.
```

### 4. Registrar o `ric1` no Non-RT RIC (`policy-agent`)

> **Nota de Troubleshooting (Docker bind mount):** Ao subir o `policy-agent` pela primeira vez sem o arquivo `application_configuration.json` no host, o Docker cria uma pasta vazia com esse nome, causando erro HTTP `500` ao tentar atualizar via API. O procedimento abaixo remove a pasta e cria o arquivo JSON real com permissão de escrita.

```bash
docker rm -f policy-agent
sudo rm -rf policy-service/config/application_configuration.json

cat <<'EOF' > policy-service/config/application_configuration.json
{
  "config": {
    "ric": [
      {
        "name": "ric1",
        "baseUrl": "http://a1-sim-osc-0:8085",
        "managedElementIds": [
          "kista_1",
          "kista_2"
        ]
      }
    ]
  }
}
EOF

chmod 666 policy-service/config/application_configuration.json
docker compose -f docker-compose.yaml -f policy-service/docker-compose.yaml up -d
```

Validação da integração Non-RT RIC ↔ Near-RT RIC:

```bash
sleep 10
curl -s http://localhost:8081/a1-policy/v2/rics | jq .
```

Retorno esperado: `ric1` com `"state": "AVAILABLE"` e `"policytype_ids": ["1"]`.

### 5. Configurar o Ambiente Python e o Servidor MCP

> **Nota de Troubleshooting (SDK MCP):** O pacote `mcp` v2.x renomeou `FastMCP` para `MCPServer`. Fixamos `"mcp<2"` para manter compatibilidade com a API v1 utilizada no servidor e nos clientes.

```bash
mkdir -p ~/agentic-rapp && cd ~/agentic-rapp
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install "mcp<2" requests google-genai ollama
```

Crie o servidor MCP (`nonrtric_mcp_server.py`) expondo as 6 ferramentas da interface A1:

- `list_near_rt_rics()` — lista RICs registrados, estados e policy types.
- `get_policy_type_schema(policytype_id)` — retorna o JSON Schema obrigatório da política.
- `list_active_policies()` — lista os IDs das políticas ativas.
- `get_policy_details(policy_id)` — consulta o payload de uma política específica.
- `apply_a1_policy(ric_id, policy_id, policytype_id, policy_data)` — cria ou atualiza uma política A1 (`service_id: "agentic-rapp"`).
- `delete_a1_policy(policy_id)` — remove uma política ativa.

Teste unitário rápido do servidor:

```bash
python3 -c "import nonrtric_mcp_server as s; print(s.list_near_rt_rics()); print(s.get_policy_type_schema('1'))"
```

### 6. Executar o rApp Agêntico

#### Opção A: LLM em Nuvem (Gemini API — Free Tier)

O script `agent_rapp.py` usa `gemini-3.5-flash-lite` com fallback automático (`gemini-3.1-flash-lite`, `gemini-3.5-flash`, `gemini-3.6-flash`) para contornar picos momentâneos de erro `503 UNAVAILABLE` e `max_output_tokens=512` para economizar cota.

- Gere uma chave gratuita em [aistudio.google.com/apikey](https://aistudio.google.com/apikey) e exporte na sessão atual:

```bash
export GEMINI_API_KEY="SUA_CHAVE_AQUI"
python3 agent_rapp.py
```

#### Opção B: LLM 100% Local na CPU (Ollama — Custo Zero de API)

Instalação do Ollama e download do modelo quantizado `qwen2.5:3b` (~1,9 GB em disco, ~2,2 GB em RAM):

```bash
curl -fsSL [https://ollama.com/install.sh](https://ollama.com/install.sh) | sh
ollama pull qwen2.5:3b
python3 agent_rapp_ollama.py
```

Também é possível passar qualquer intenção customizada via argumento:

```bash
python3 agent_rapp.py "Aumente o priorityLevel da politica pol-urllc-01 para 25 por congestionamento no slice."
```

---

## Validação Independente (Fora do Agente)

Para comprovar que a política foi aplicada no Non-RT RIC (PMS) e propagada via interface A1 para o Near-RT RIC simulado:

```bash
# 1. Listar todas as políticas no Non-RT RIC (PMS)
curl -s http://localhost:8081/a1-policy/v2/policies | jq .

# 2. Conferir detalhes no PMS (porta 8081)
curl -s http://localhost:8081/a1-policy/v2/policies/pol-urllc-01 | jq .

# 3. Conferir propagação real no Near-RT RIC simulado (porta 8085)
curl -s http://localhost:8085/a1-p/policytypes/1/policies/pol-urllc-01 | jq .
curl -s http://localhost:8085/a1-p/policytypes/1/policies/pol-local-01 | jq .
```

---

## Resultados Empíricos Obtidos (Nuvem vs. Local CPU)

| Dimensão Avaliada                 | Nuvem (`gemini-3.5-flash-lite`)                               | Local CPU (`qwen2.5:3b` via Ollama)                                  |
| :-------------------------------- | :------------------------------------------------------------ | :------------------------------------------------------------------- |
| **Política criada**               | `pol-urllc-01` (`ue-001`, `slice-urllc`, `priorityLevel: 10`) | `pol-local-01` (`ue-002`, `slice-embb`, `priorityLevel: 15`)         |
| **Status HTTP na interface A1**   | `201 Created` (schema válido)                                 | `201 Created` (schema válido)                                        |
| **Chamadas MCP executadas**       | 4 (fluxo mínimo exato)                                        | 4 (repetiu `list_near_rt_rics`; pulou `list_active_policies` no fim) |
| **Latência da camada MCP + A1**   | Milissegundos                                                 | `9 ms` a `24 ms` por operação                                        |
| **Latência de inferência do LLM** | ~1–2 s por passo                                              | `5,15 s` a `17,63 s` por passo (`60,61 s` total)                     |
| **Custo de API**                  | Free Tier (sujeito a cota/503)                                | R$ 0 (100% offline na VM)                                            |

---

## Próximos Passos Planejados

1. **Loop Fechado (ZSM):** adicionar ferramenta MCP `get_cell_kpis(cell_id)` lendo métricas simuladas de PRB/latência para o rApp detectar anomalias e ajustar políticas A1 autonomamente.
2. **Benchmark Automatizado:** registrar em CSV latência por passo, validade de argumentos, taxa de sucesso e consumo de tokens comparando `gemini-3.5-flash-lite`, `qwen2.5:3b` e `qwen2.5:7b`.
3. **Camada de Governança (Guardian/Validador):** validar limites operacionais (ex.: faixa permitida de `priorityLevel` e conflito entre slices) antes de confirmar o `PUT` no PMS.
