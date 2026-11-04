"""
faixas_powerbi.py - Converte as faixas operativas publicadas nos painéis Power BI da SOE em config/faixas.json.

Entrada: CSV gerado por scripts/powerbi_faixas_soe.py (';', vírgula decimal, UTF-8 com BOM), colunas
sistema;reservatorio;faixa_vigente;periodo_hidrologico;vu_referencia_pct;...;data_extracao.
Saída: config/faixas.json -> {"faixas": {"<rid>|<período>": {...}}}. O período segue a definição de cada norma
(mês 'aaaa-mm' ou sábado de início da semana operativa, Paranapanema). Entradas anteriores são preservadas,
formando o histórico das faixas publicadas.

Uso: python src/faixas_powerbi.py <faixas_operativas_vigentes.csv> [config/faixas.json]
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regras import FAIXAS  # noqa: E402

RESERVATORIO = {"sistema equivalente cantareira": "CANTAREIRA", "emborcacao": "PREMBO", "furnas": "GRFURN",
                "itumbiara": "PRITUM", "m. moraes": "GRMMOR", "jurumirim": "PPJURU", "chavantes": "PPCHAV",
                "capivara": "PPCAPI", "tres marias": "SFTMAR", "sobradinho": "SFSOBR", "serra da mesa": "TOSMES"}
NOMES_CANONICOS = ["especial", "restricao", "alerta", "atencao", "normal"]  # ordem de busca no texto


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def indice_faixa(rid: str, texto: str, d: dt.date) -> tuple[str, int]:
    cfg = FAIXAS[rid]
    nomes = cfg["nomes"]
    if "umido" in cfg and "umido" in sem_acento(texto):
        nomes = cfg["nomes_umido"]
    t = sem_acento(texto)
    for chave in NOMES_CANONICOS:
        if chave in t:
            for i, n in enumerate(nomes):
                if chave in sem_acento(n):
                    return n, i
    raise ValueError(f"faixa '{texto}' não reconhecida para {rid} (nomes: {nomes})")


def periodo(rid: str, d: dt.date) -> str:
    if FAIXAS[rid]["definicao"] == "semana_sexta":
        return (d - dt.timedelta(days=(d.weekday() - 5) % 7)).isoformat()
    return d.strftime("%Y-%m")


def main():
    origem = Path(sys.argv[1])
    destino = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "config" / "faixas.json")
    atual = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    faixas = atual.get("faixas", {})
    with origem.open(encoding="utf-8-sig", newline="") as f:
        for lin in csv.DictReader(f, delimiter=";"):
            rid = RESERVATORIO.get(sem_acento(lin["reservatorio"]))
            if not rid:
                print(f"ignorado: {lin['reservatorio']}")
                continue
            d = dt.date.fromisoformat((lin.get("data_extracao") or "")[:10])
            texto = lin["faixa_vigente"] + " " + (lin.get("periodo_hidrologico") or "")
            nome, idx = indice_faixa(rid, texto, d)
            vu = lin.get("vu_referencia_pct") or ""
            k = f"{rid}|{periodo(rid, d)}"
            if (faixas.get(k) or {}).get("origem") == "Boletim ANA":  # boletim oficial já registrado prevalece
                print(f"{k:22} mantido (boletim ANA)")
                continue
            faixas[k] = {"faixa": nome, "idx": idx, "texto_painel": lin["faixa_vigente"],
                         "periodo_hidrologico": lin.get("periodo_hidrologico") or None,
                         "vu_ref": float(vu.replace(",", ".")) if vu else None,
                         "limites": lin.get("limites") or None, "condicao": lin.get("condicao_restricao") or None,
                         "atualizacao_painel": lin.get("ultima_atualizacao_modelo"), "extraido_em": lin.get("data_extracao"),
                         "url": lin.get("url_painel")}
            print(f"{k:22} {nome:20} (painel: {lin['faixa_vigente']})")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"fonte": "Boletins diários ANA (gov.br) e painéis Power BI SOE/ANA", "atualizado_em": dt.datetime.now().isoformat(timespec="seconds"),
                                   "faixas": dict(sorted(faixas.items()))}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {destino} ({len(faixas)} entradas)")


if __name__ == "__main__":
    main()
