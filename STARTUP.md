# Guia de Inicialização e Desligamento (Pós-Restart da VM)

Use este roteiro sempre que desligar e religar a VM na Google Cloud (ou reiniciar o Docker) para deixar o ambiente pronto para rodar os testes da **Fase 1 (IBN)** e da **Fase 2 (Loop Fechado ZSM)**, e para encerrar tudo de forma limpa ao final do uso.

> **Por que este passo é necessário?** O container `policy-agent` mantém a configuração do `ric1` salva em disco (`application_configuration.json`), mas o simulador `a1-sim-osc-0` armazena o `Policy Type 1` apenas em memória RAM. Ao reiniciar a VM, é obrigatório recadastrar o schema de QoS no simulador.

---

## 1. Subir os containers do O-RAN

```bash
docker start a1-sim-osc-0 policy-agent
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

*Verifique se ambos (`policy-agent` na porta `8081` e `a1-sim-osc-0` na porta `8085`) aparecem como `Up`.*

*(Caso o `docker start` indique que os containers foram removidos, recrie-os seguindo os passos 1, 2 e 4 do `README.md` principal.)*

---

## 2. Recadastrar o Policy Type `1` (`QoS_Policy`) no Near-RT RIC Simulado

Aguarde ~5 segundos após subir os containers e execute:

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
```
*Retorno esperado:* `Policy type 1 is OK.`

---

## 3. Validar a sincronização Non-RT RIC ↔ Near-RT RIC

Aguarde ~10 segundos para o `policy-agent` sincronizar com o simulador (se o `policytype_ids` ainda vier vazio, reinicie apenas o `policy-agent` com `docker restart policy-agent` e aguarde 10s):

```bash
curl -s http://localhost:8081/a1-policy/v2/rics | jq .
```

**Retorno esperado:**
```json
{
  "rics": [
    {
      "ric_id": "ric1",
      "managed_element_ids": ["kista_1", "kista_2"],
      "policytype_ids": ["1"],
      "state": "AVAILABLE"
    }
  ]
}
```

---

## 4. Ativar o ambiente Python (e verificar o Ollama)

```bash
cd ~/agentic-rapp
source .venv/bin/activate

# Opcional: confirme se o serviço do Ollama subiu automaticamente com a VM
ollama list
```

*(Se for usar os scripts com **Gemini**, exporte a chave novamente na sessão atual: `export GEMINI_API_KEY="SUA_CHAVE_AQUI"`.)*

---

## 5. Executar os Testes

### Fase 1 — rApp Agêntico (IBN sob demanda)
```bash
# Local (CPU / Ollama - custo zero)
python3 agent_rapp_ollama.py

# Nuvem (Gemini API)
python3 agent_rapp.py
```

### Fase 2 — Loop Fechado ZSM (3 terminais)
* **Terminal 1 (Simulador O1):**
  ```bash
  cd ~/agentic-rapp && source .venv/bin/activate
  python3 zsm_closed_loop/kpi_simulator.py
  ```
* **Terminal 2 (Agente ZSM — escolha Ollama ou Gemini):**
  ```bash
  cd ~/agentic-rapp && source .venv/bin/activate
  python3 zsm_closed_loop/zsm_rapp_ollama.py
  # ou: python3 zsm_closed_loop/zsm_rapp_gemini.py
  ```
* **Terminal 3 (Injetar congestionamento):**
  ```bash
  curl -s -X POST http://localhost:8090/scenario/congestion | jq .
  ```

---

## 6. Procedimento de Desligamento (Stop do Ambiente)

Siga esta ordem ao terminar os testes para encerrar os processos sem corromper estados e evitar cobrança desnecessária na Google Cloud:

### 6.1. Encerrar os scripts Python (Agentes e Simulador)
* Nos terminais onde `zsm_rapp_ollama.py` / `zsm_rapp_gemini.py` e `kpi_simulator.py` estiverem rodando, pressione **`Ctrl + C`**.
* Se algum script tiver ficado em segundo plano, garanta o encerramento:
  ```bash
  pkill -f "zsm_rapp_|kpi_simulator.py|zsm_mcp_server.py|nonrtric_mcp_server.py"
  ```

### 6.2. Descarregar o modelo local do Ollama (opcional)
```bash
ollama stop qwen2.5:3b
```

### 6.3. Parar os containers do O-RAN de forma limpa
Use `docker stop` (e **não** `docker rm`), assim os containers e o arquivo `application_configuration.json` permanecem preservados para o próximo `docker start`:

```bash
docker stop policy-agent a1-sim-osc-0
docker ps
```
*Verificação:* o `docker ps` deve retornar vazio (nenhum container rodando).

### 6.4. Desligar a VM na Google Cloud
Você pode desligar direto pelo terminal da VM (o GCP muda o status para `TERMINATED`/`STOPPED` automaticamente) ou pelo console do Google Cloud:

```bash
sudo shutdown -h now
```
*(Ou, pelo seu MacBook via `gcloud`: `gcloud compute instances stop vm-lab-rapp-agentico-mcp-non-rt-ric --zone=SUA_ZONA`.)*
