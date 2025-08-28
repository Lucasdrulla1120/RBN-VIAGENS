# RBN Viagens - Deploy Grátis (Koyeb)

Este projeto foi configurado para rodar na Koyeb (camada gratuita) usando Dockerfile.

## Estrutura
- app.py (aplicação Flask)
- wsgi.py (ponto de entrada WSGI)
- requirements.txt (dependências)
- Dockerfile (build e run)

## Variáveis de Ambiente (na Koyeb)
- `UPLOAD_DIR=/app/uploads`
- `DB_PATH=/app/data/rbn_trip_expenses.db`

## Volumes (na Koyeb)
- Volume 1: montado em `/app/uploads`
- Volume 2: montado em `/app/data`

## Rodando local
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# ou
source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt
python app.py
```
