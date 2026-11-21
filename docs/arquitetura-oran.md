# Conceitos de Arquitetura O-RAN e Mapeamento do Lab

Este documento resume os conceitos fundamentais da arquitetura **Open RAN (O-RAN)** utilizados neste laboratório, relacionando cada função teórica aos containers e scripts executados na PoC.

---

## 1. Mapa dos Conceitos Básicos do O-RAN no Lab

A arquitetura Open RAN desagrega a inteligência da rede de rádio em duas camadas de tempo diferentes dentro do **RIC (RAN Intelligent Controller)**:

| Conceito O-RAN | Escala de Tempo | Função na Rede Real | O que representa no Lab |
| :--- | :--- | :--- | :--- |
| **Non-RT RIC** | **> 1 segundo** (estratégico) | Camada de orquestração e políticas de longo prazo (roda dentro do SMO). | Container `policy-agent` (porta `8081`). |
| **A1 PMS** (*Policy Management Service*) | > 1 segundo | Serviço dentro do Non-RT RIC que mantém o catálogo de Near-RT RICs, valida e gerencia o ciclo de vida das políticas A1. | API REST `http://localhost:8081/a1-policy/v2` dentro do `policy-agent`. |
| **rApp** | > 1 segundo | Aplicação que roda no Non-RT RIC para tomar decisões estratégicas, analisar tendências e emitir políticas A1. | Scripts `agent_rapp.py` e `agent_rapp_ollama.py` (conectados via MCP). |
| **Interface A1** | > 1 segundo | Interface padronizada pela O-RAN Alliance que conecta o Non-RT RIC ao Near-RT RIC para envio de políticas, dados de enriquecimento e modelos de IA. | Comunicação HTTP entre o `policy-agent` (`8081`) e o `a1-sim-osc-0` (`8085`). |
| **Near-RT RIC** | **10 ms a 1 s** (tático / quase tempo real) | Camada próxima à antena que recebe as políticas A1 e coordena ajustes rápidos de rádio. | Container `a1-sim-osc-0` (`ric1`, porta `8085`). |
| **xApp** | 10 ms a 1 s | Aplicação que roda dentro do Near-RT RIC. Lê a política A1 recebida e executa o controle fino (alocação de PRBs, handover, beamforming). | Emulado internamente pelo `a1-sim-osc-0` (recebe e armazena a política A1). |
| **Interface E2** | 10 ms a 1 s | Interface pela qual o Near-RT RIC (xApps) comanda diretamente os nós de rádio físicos/virtualizados (gNB: CU/DU). | Representada simbolicamente pelos `managedElementIds`: `kista_1` e `kista_2`. |
| **Interface O1** | Segundos / minutos | Interface de gerenciamento tradicional (FCAPS: falhas, configuração, telemetria/KPIs) entre o SMO/Non-RT RIC e os elementos da rede. | Prevista para a etapa seguinte de coleta de KPIs simulados (loop fechado ZSM). |

---

## 2. Como Tudo se Encaixa em um Exemplo Real

Imagine uma antena 5G (gNB `kista_1`) atendendo simultaneamente celulares comuns (vídeo/streaming) e um braço robótico industrial (`ue-001` no `slice-urllc`):

1. **Na camada lenta (Non-RT RIC — > 1 s):** um **rApp** (neste lab, o agente LLM) recebe a intenção do operador ou identifica via telemetria (O1) que o tráfego do `ue-001` precisa de mais prioridade. Ele envia uma **A1 Policy** para o `ric1` definindo `priorityLevel: 25` via **A1 PMS**.
2. **Na camada rápida (Near-RT RIC — 10 ms a 1 s):** um **xApp** de controle de QoS recebe essa política A1 e, a cada dezenas de milissegundos, traduz esse `priorityLevel: 25` em comandos concretos via **interface E2** para o escalonador da gNB reservar blocos de rádio (PRBs) para o `ue-001`.
3. **Por que o LLM atua como rApp e não como xApp?** O raciocínio de um LLM leva segundos (neste lab, ~1–2 s na nuvem e ~60 s no `qwen2.5:3b` em CPU), o que é compatível com o ciclo de políticas A1 do **Non-RT RIC**, mas inviável para o limite de milissegundos da **interface E2** no Near-RT RIC.

---

## 3. Onde Entra o MCP nessa Arquitetura

O **MCP (Model Context Protocol)** não faz parte da especificação original da O-RAN Alliance — ele funciona como uma camada de adaptação no **Non-RT RIC** para que o LLM consiga enxergar e operar o **A1 PMS** de forma segura e padronizada.

Sem o MCP, o LLM apenas gera texto; com o `nonrtric_mcp_server.py`, ele passa a invocar ferramentas tipadas para consultar os RICs disponíveis, ler o JSON Schema do *Policy Type* e aplicar a política A1 real sem acoplamento direto ao modelo de IA utilizado.

---

## 4. Mapeamento nos Planos da Arquitetura AI-Native (5G/6G)

Relacionando os componentes do lab aos três planos da arquitetura AI-native descrita em Mazene et al. (2026):

* **Management Plane (SMO / ZSM / IBN):** onde vive o **Non-RT RIC**, incluindo o container `policy-agent` (A1 PMS). A comunicação entre o Management Plane e o domínio de RAN no Control Plane ocorre via **interface A1** (políticas) e **interface O1** (telemetria/gestão).
* **Control Plane (Radio Access Network):** onde entra o container `a1-sim-osc-0` (`ric1`), representando o **Near-RT RIC + xApps** responsáveis pelo controle de recursos da gNB em quase tempo real (10 ms a 1 s).
* **AI-Native Plane (AI Agents / DataOps / XAI):** onde atuam o **Servidor MCP** (`nonrtric_mcp_server.py`) e os **rApps Agênticos** (`agent_rapp.py` e `agent_rapp_ollama.py`), injetando raciocínio autônomo no SMO/Management Plane.

---

## 5. Distribuição Física em Produção (Torre vs. Edge vs. Cloud)

Em uma rede O-RAN real, as funções não rodam no mesmo servidor físico — elas são distribuídas geograficamente de acordo com o limite de latência de cada loop de controle:

| Camada Física | Distância da Antena | O que roda em produção | O que representa no Lab | Por que fica nessa camada? |
| :--- | :--- | :--- | :--- | :--- |
| **Cell Site / Torre (Far Edge)** | **0 km** (na antena ou base da torre) | **O-RU** (rádio físico) e geralmente **O-DU** (camada física/MAC da gNB). | Representado simbolicamente por `kista_1` e `kista_2`. | Exige latência de **microssegundos** (< 250 µs no fronthaul) para transmissão de sinal de rádio sem perda. |
| **Edge Regional / Metropolitano** | **~10 a 40 km** (datacenter local da operadora) | **O-CU** (camadas superiores da gNB) + **Near-RT RIC** + **xApps**. | Container `a1-sim-osc-0` (`ric1`, porta `8085`). | O loop da interface **E2** exige **10 ms a 1 s**; ficar no Edge permite que um Near-RT RIC controle dezenas de gNBs da região com baixa latência. |
| **Nuvem Central (Central Cloud)** | **Centenas de km** (datacenter central ou nuvem como GCP/AWS) | **SMO** + **Non-RT RIC** + **A1 PMS** + **rApps** + **LLMs / GPUs**. | Container `policy-agent` (`8081`) + `nonrtric_mcp_server.py` + `agent_rapp.py`. | O loop da interface **A1** opera em **> 1 segundo** (segundos/minutos). Ficar na nuvem dá visão global da rede e facilita rodar LLMs e bases de dados pesadas. |

### Resumo Prático (Lab vs. Produção)

* **Neste lab:** a "Nuvem Central" (`policy-agent` + rApp LLM) e o "Edge Regional" (`a1-sim-osc-0`) rodam lado a lado na mesma VM do GCP por praticidade e custo.
* **Em produção:** o `policy-agent` + rApp Agêntico continuam na **Cloud Central**, enquanto múltiplos **Near-RT RICs** ficam distribuídos em servidores **Edge** próximos às cidades/antenas, recebendo as políticas A1 pela rede de transporte.

---
## Referência

* Ameur, M., Mekrache, A., Brik, B., & Ksentini, A. (2026). *LLM-Powered Agentic AI for 5G/6G Networks: A Tutorial and Survey on Architectures, Protocols, and Standardization*. arXiv:2607.16066.
