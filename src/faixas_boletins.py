"""
faixas_boletins.py - Incorpora a config/faixas.json o histórico de faixas publicadas nos boletins diários da
Sala de Situação da ANA (gov.br), gerado por historico_faixas/scripts/consolidar_vigencias.py.

Entrada: faixas_historico_vigencias.csv (';', vírgula decimal, UTF-8 com BOM), uma linha por reservatório e período.
Saída: config/faixas.json no formato já usado pelo painel ("<rid>|<período>"), com "origem": "Boletim ANA".
Prioridade (decisão SOE de 04/11/2026): faixa publicada (boletim oficial; Power BI quando não há boletim) prevalece
sobre a calculada; o cálculo pela norma fica como conferência (divergencias_faixa no painel.json).
Entradas de boletim substituem entradas do Power BI do mesmo período; as demais são preservadas.

Uso: python src/faixas_boletins.py <faixas_historico_vigencias.csv> [config/faixas.json]
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regras import FAIXAS  # noqa: E402


def num(s):
    return float(s.replace(",", ".")) if s not in (None, "") else None


def main():
    origem = Path(sys.argv[1])
    destino = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "config" / "faixas.json")
    atual = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    faixas = atual.get("faixas", {})
    n_novos = n_subst = 0
    with origem.open(encoding="utf-8-sig", newline="") as f:
        for lin in csv.DictReader(f, delimiter=";"):
            rid, per = lin["rid"], lin["periodo"]
            if rid not in FAIXAS:
                print(f"ignorado: {rid}")
                continue
            nomes = FAIXAS[rid]["nomes"]
            if lin["faixa"] not in nomes:
                raise ValueError(f"{rid}|{per}: faixa '{lin['faixa']}' fora de {nomes}")
            k = f"{rid}|{per}"
            if k in faixas:
                n_subst += 1
            else:
                n_novos += 1
            faixas[k] = {"faixa": lin["faixa"], "idx": nomes.index(lin["faixa"]), "origem": "Boletim ANA",
                         "texto_painel": None, "periodo_hidrologico": None, "vu_ref": num(lin.get("valor_referencia")),
                         "limites": lin.get("condicao_limites") or None, "condicao": None,
                         "boletim": lin["boletim_origem"], "url": lin["url_oficial"], "status": lin["status"],
                         "vigencia": [lin["vigencia_inicio"], lin["vigencia_fim"]]}
    destino.write_text(json.dumps({"fonte": "Boletins diários ANA (gov.br) e painéis Power BI SOE/ANA",
                                   "atualizado_em": dt.datetime.now().isoformat(timespec="seconds"),
                                   "faixas": dict(sorted(faixas.items()))}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {destino}: {n_novos} novas, {n_subst} substituídas, {len(faixas)} no total")


if __name__ == "__main__":
    main()
