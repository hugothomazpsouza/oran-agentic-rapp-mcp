cat <<'EOF' > ~/agentic-rapp/zsm_closed_loop/README.md

# Fase 2 — Loop Fechado ZSM (Zero-touch Service Management) com Telemetria O1 e Controle A1

Este diretório contém uma implementação **100% independente** da Fase 1 do laboratório. Aqui, o rApp Agêntico evolui de comandos manuais (IBN) para um **ciclo fechado autônomo (ZSM)**: o sistema monitora continuamente KPIs de rádio simulados, detecta violações de SLA, executa análise de causa raiz (RCA) via **MCP** e aplica políticas corretivas na **interface A1** do O-RAN sem intervenção humana.

Para evitar desperdício de CPU ou cota de API, o loop implementa um mecanismo de **gating (standby)**: enquanto a rede está saudável, o LLM não é acionado.

---

## Arquitetura do Loop Fechado

```text
+-----------------------------------------------------------------------+
|  Terminal 3 (Injeção de Falha / Validação)                            |
|  POST :8090/scenario/congestion  |  GET :8081 e :8085 (Prova Real)    |
+----------------------------------+------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
|  Simulador de Telemetria O1 (kpi_simulator.py — Porta 8090)           |
|  Gera KPIs (PRB, latência, perda) para kista_1 e kista_2              |
|  * Consulta o PMS (:8081) em tempo real para reagir à política A1     |
+------------------+-----------------------------------+----------------+
                   | GET /kpis (Monitoramento)         ^
                   v                                   | MCP Tool:
+------------------------------------------+           | get_cell_kpis()
|  rApp Agêntico ZSM (Terminal 2)          |           |
|  - zsm_rapp_ollama.py (Local CPU / 3B)   +-----------+
|  - zsm_rapp_gemini.py (Nuvem Flash-Lite) |
+------------------+-----------------------+
                   | MCP (JSON-RPC 2.0 via stdio)
                   v
+-----------------------------------------------------------------------+
|  Servidor MCP ZSM (zsm_mcp_server.py)                                 |
|  Tools: get_cell_kpis, list_near_rt_rics, get_policy_type_schema,     |
|         list_active_policies, apply_a1_policy                         |
+------------------+----------------------------------------------------+
                   | REST HTTP (:8081/a1-policy/v2)
                   v
+-----------------------------------------------------------------------+
|  O-RAN SC Non-RT RIC (policy-agent :8081)                             |
|       └── Interface A1 ──> Near-RT RIC Simulado (a1-sim-osc-0 :8085)  |
+-----------------------------------------------------------------------+
```

---

## Estrutura e Explicação dos Componentes

```text
zsm_closed_loop/
├── README.md              # Esta documentação
├── kpi_simulator.py       # Simulador de telemetria O1 (HTTP :8090)
├── zsm_mcp_server.py      # Servidor MCP exclusivo da Fase 2 (O1 + A1)
├── zsm_rapp_ollama.py     # Loop fechado ZSM usando Ollama local (qwen2.5:3b)
└── zsm_rapp_gemini.py     # Loop fechado ZSM usando Gemini API (nuvem)
```

| Arquivo              | Papel na Arquitetura       | O que faz na prática                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| :------------------- | :------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `kpi_simulator.py`   | **Telemetria O1 Simulada** | Sobe um servidor HTTP na porta `8090` simulando as células `kista_1` (`ue-001` / `slice-urllc`, SLA de latência $\le 10\text{ ms}$) e `kista_2` (`ue-002` / `slice-embb`). Em estado normal, a latência da `kista_1` varia entre `3.8` e `5.5 ms`. Quando recebe `POST /scenario/congestion`, a latência sobe para `24`–`32 ms` (`SLA_VIOLATED`). Ele consulta o `policy-agent` (`:8081`) a cada leitura: assim que detecta uma política A1 ativa para o `ue-001` com `priorityLevel >= 20`, reduz a latência para `5.5`–`7.8 ms` (`MITIGATED_BY_A1_POLICY`). |
| `zsm_mcp_server.py`  | **MCP Server (O1 + A1)**   | Expõe 5 ferramentas tipadas via JSON-RPC 2.0 (`stdio`): `get_cell_kpis` (lê a porta `8090`) e `list_near_rt_rics`, `get_policy_type_schema`, `list_active_policies`, `apply_a1_policy` (integradas ao `policy-agent` na porta `8081`). É compartilhado pelos dois agentes sem alteração de código.                                                                                                                                                                                                                                                            |
| `zsm_rapp_ollama.py` | **rApp ZSM Local (CPU)**   | Monitora `/kpis` a cada 10s. Mantém o LLM em standby enquanto a latência está $\le 10\text{ ms}$. Ao detectar violação, aciona o **`qwen2.5:3b`** via Ollama (custo zero, offline) para diagnosticar, ler o JSON Schema e aplicar a política `pol-zsm-ollama` (`priorityLevel: 25`), medindo o tempo de inferência vs. tempo MCP/A1.                                                                                                                                                                                                                          |
| `zsm_rapp_gemini.py` | **rApp ZSM Nuvem**         | Mesmo fluxo de monitoramento (a cada 8s) e remediação autônoma, usando **`gemini-3.5-flash-lite`** (com fallback automático em caso de `503 UNAVAILABLE`) e aplicando a política `pol-zsm-gemini` (`priorityLevel: 25`).                                                                                                                                                                                                                                                                                                                                      |

---

## Pré-requisitos

Antes de iniciar, confirme que a infraestrutura base da Fase 1 está rodando:

```bash
# 1. Containers O-RAN ativos (policy-agent e a1-sim-osc-0)
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 2. Non-RT RIC enxergando o ric1 como AVAILABLE
curl -s http://localhost:8081/a1-policy/v2/rics | jq .
```

---

## Passo a Passo: Como Executar os Testes

O experimento utiliza **3 terminais** simultâneos na VM (ou 3 abas SSH/tmux).

### Passo 0 — Limpar políticas antigas (para começar com `active_a1_priority = 0`)

```bash
curl -s -X DELETE http://localhost:8081/a1-policy/v2/policies/pol-urllc-01
curl -s -X DELETE http://localhost:8081/a1-policy/v2/policies/pol-local-01
curl -s -X DELETE http://localhost:8081/a1-policy/v2/policies/pol-zsm-ollama
curl -s -X DELETE http://localhost:8081/a1-policy/v2/policies/pol-zsm-gemini

# Confirma que a lista está vazia: deve retornar {"policy_ids": []}
curl -s http://localhost:8081/a1-policy/v2/policies | jq .
```

_(Respostas `404` durante o `DELETE` são normais — apenas indicam que aquela política já não existia)._

---

### Terminal 1 — Subir o Simulador de Telemetria O1 (deixe rodando)

```bash
cd ~/agentic-rapp
source .venv/bin/activate
python3 zsm_closed_loop/kpi_simulator.py
```

**Saída esperada:**

```text
[KPI Simulator] Rodando em http://localhost:8090/kpis (Ctrl+C para sair)
```

---

### Terminal 2 — Iniciar o Loop Fechado ZSM (escolha Ollama ou Gemini)

#### Opção A: 100% Local na CPU (Ollama — Custo Zero)

```bash
cd ~/agentic-rapp
source .venv/bin/activate
python3 zsm_closed_loop/zsm_rapp_ollama.py
```

#### Opção B: Nuvem (Gemini API — Free Tier)

```bash
cd ~/agentic-rapp
source .venv/bin/activate
export GEMINI_API_KEY="SUA_CHAVE_AQUI"
python3 zsm_closed_loop/zsm_rapp_gemini.py
```

**Saída esperada em estado normal (LLM em standby):**

```text
 [MONITOR OK] kista_1 | PRB: 46.6% | Latencia: 4.3 ms | Prio A1: 0.0 | Status: HEALTHY (LLM standby)
```

---

### Terminal 3 — Injetar o Congestionamento na Célula `kista_1`

```bash
curl -s -X POST http://localhost:8090/scenario/congestion | jq .
```

**O que acontece automaticamente no Terminal 2:**

1. No próximo ciclo, o monitor detecta `[ANOMALIA SLA] kista_1 | Latencia: ~29 ms (> 10 ms) | PRB: ~94%`.
2. O rApp acorda o LLM e encadeia as chamadas MCP:
   `get_cell_kpis()` → `list_near_rt_rics()` → `get_policy_type_schema("1")` → `apply_a1_policy(...)`.
3. O simulador percebe a nova política A1 (`priorityLevel: 25`) no `policy-agent` e reduz a latência do slice URLLC para ~6 ms.
4. O Terminal 2 volta automaticamente para `[MONITOR OK] ... Status: MITIGATED_BY_A1_POLICY (LLM standby)`.

---

## Validações Independentes (Fora do Agente)

Execute no **Terminal 3** após a remediação para provar que a política foi aplicada no Non-RT RIC e propagada pela interface A1 até o Near-RT RIC simulado:

```bash
# 1. Conferir KPIs atuais no simulador (deve mostrar MITIGATED_BY_A1_POLICY e active_a1_priority: 25.0)
curl -s http://localhost:8090/kpis | jq .

# 2. Listar todas as políticas ativas no Non-RT RIC (PMS - porta 8081)
curl -s http://localhost:8081/a1-policy/v2/policies | jq .

# 3. Validar a política criada pelo Ollama diretamente no Near-RT RIC simulado (porta 8085)
curl -s http://localhost:8085/a1-p/policytypes/1/policies/pol-zsm-ollama | jq .

# 4. Validar a política criada pelo Gemini diretamente no Near-RT RIC simulado (porta 8085)
curl -s http://localhost:8085/a1-p/policytypes/1/policies/pol-zsm-gemini | jq .
```

---

## Como Parar os Testes e Resetar o Ambiente

1. **Parar o agente ZSM (Terminal 2):** pressione `Ctrl + C`. Isso encerra o script e fecha automaticamente o subprocesso `zsm_mcp_server.py`.
2. **Parar o simulador de KPIs (Terminal 1):** pressione `Ctrl + C`. _(Se for apenas trocar de Ollama para Gemini, pode manter o Terminal 1 rodando)._
3. **Liberar a RAM do modelo Ollama imediatamente (opcional):**
   ```bash
   ollama stop qwen2.5:3b
   ```
4. **Resetar o cenário para uma nova rodada de testes (Terminal 3):**
   ```bash
   curl -s -X POST http://localhost:8090/scenario/normal | jq .
   curl -s -X DELETE http://localhost:8081/a1-policy/v2/policies/pol-zsm-ollama
   curl -s -X DELETE http://localhost:8081/a1-policy/v2/policies/pol-zsm-gemini
   ```

---

## Resultados Empíricos Registrados (Ollama `qwen2.5:3b` em CPU)

| Métrica                                                 | Valor Observado                                           |
| :------------------------------------------------------ | :-------------------------------------------------------- |
| **Latência antes da remediação (`SLA_VIOLATED`)**       | `29.4 ms` (PRB `94.8%`)                                   |
| **Latência após remediação (`MITIGATED_BY_A1_POLICY`)** | `6.2 ms` (PRB `87.6%`, `priorityLevel: 25`)               |
| **Tempo total do ciclo agêntico (5 passos)**            | `64.99 s`                                                 |
| **Tempo total da camada de rede (4 chamadas MCP + A1)** | `69 ms` (`21 ms + 10 ms + 10 ms + 28 ms` — 0,1% do total) |
| **Tempo de inferência do LLM em CPU**                   | `~64.92 s` (99,9% do total)                               |
| **Validade do payload A1**                              | `HTTP 201 Created` (100% aderente ao JSON Schema)         |

EOF
