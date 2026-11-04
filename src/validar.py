"""
validar.py - Confere o cálculo (data/painel.json) com a "Planilha de Auditoria Multitemporal de Eventos SOE".

Uso:
    python src/validar.py <planilha.xlsx> [painel.json] [saida.csv]

Para cada linha da planilha (data × ponto de controle), compara o valor auditado e o status com o resultado do
painel. Resultado: CSV linha a linha e resumo por aba e ponto (valores iguais com diferença ≤ 2% e status iguais).
Dependência: openpyxl (pip install openpyxl).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

MAPA = {
    "Intradiária": {"Paraibuna": "1382-PBPARA", "Santa Branca": "1382-PBSTAB", "Jaguari": "1382-PBJAGU", "Funil": "1382-PBFUNI",
                    "Santa Cecília (Jusante)": "1382-PBSTAC", "Pereira Passos": "1382-RLPPAS",
                    "UHE Jaguari - Captação Interligação": "1931-max"},
    "Diaria_SIN": {"Furnas": "193-GRFURN", "Peixoto (M. M. Moraes)": "193-GRMMOR", "Cascata Marimbondo": "193-GRMARI",
                   "Cascata Água Vermelha": "193-GRAGVL", "Emborcação": "194-PREMBO", "Itumbiara": "194-PRITUM",
                   "Cascata São Simão": "194-PRSSIM", "Três Marias": "2081-SFTMAR-min", "Sobradinho": "2081-SFSOBR-min",
                   "Xingó": "2081-SFXING-min", "Itaparica (Luiz Gonzaga)": "2081-SFLGON", "Serra da Mesa": "70-TOSMES-min",
                   "Estreito": "70-TOUHET", "Foz do Chapecó": "49-RIUHFC", "Santa Cecília Guandu": "1382-PBSTAC-guandu",
                   "Elevatória Santa Inês": "925-santaines", "Jaguari-Jacareí": "614-29001", "Cota Cheia Cachoeira": "614-29002",
                   "Cota Cheia Atibainha": "614-29003", "Cota Cheia Paiva Castro": "614-29004",
                   "Valinhos (Vazão Mínima)": "925-valinhos-dia", "Atibaia (Vazão Mínima)": "925-atibaia-dia",
                   "Buenópolis (Vazão Mínima)": "925-buenopolis-dia"},
    "Semanal_Paranapanema": {"Jurumirim": "132-PPJURU", "Chavantes": "132-PPCHAV", "Capivara": "132-PPCAPI"},
    "Movel_PCJ": {"Valinhos (3D-007T)": "925-valinhos-movel", "Atibaia (3E-063T)": "925-atibaia-movel", "Buenópolis (3D-009T)": "925-buenopolis-movel"},
    "Mensal_Enquadramentos": {"Furnas": "193-GRFURN", "Peixoto (M. M. Moraes)": "193-GRMMOR", "Emborcação": "194-PREMBO",
                              "Itumbiara": "194-PRITUM", "Três Marias": "2081-SFTMAR-max", "Sobradinho": "2081-SFSOBR-max",
                              "Serra da Mesa": "70-TOSMES-max", "Elevatória Santa Inês": "925-santaines"},
    "Transposicao_Anual": {"Transposição Jaguari-Atibainha": "1931-volume"},
}


def num(txt):
    if txt is None:
        return None
    if isinstance(txt, (int, float)):
        return float(txt)
    m = re.search(r"-?[\d.]+,\d+|-?\d+", str(txt))
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def data(txt):
    if isinstance(txt, dt.datetime):
        return txt.date().isoformat()
    try:
        return dt.datetime.strptime(str(txt)[:10], "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def status_base(txt):
    t = (txt or "").split("(")[0].strip().lower()
    return {"atende": "Atende", "não atende": "Não atende", "nao atende": "Não atende", "tolerância": "Tolerância"}.get(t, txt)


def main():
    xlsx = Path(sys.argv[1])
    painel = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else "data/painel.json").read_text(encoding="utf-8"))
    saida = Path(sys.argv[3] if len(sys.argv) > 3 else "data/validacao.csv")
    idx = {r["id"]: {s["d"]: s for s in r["serie"]} for r in painel["regras"]}
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    linhas, resumo = [], defaultdict(lambda: [0, 0, 0, 0])
    for aba, mapa in MAPA.items():
        if aba not in wb.sheetnames:
            continue
        for row in list(wb[aba].iter_rows(values_only=True))[1:]:
            ponto = row[2]
            rid = mapa.get(ponto)
            d = data(row[0])
            if not rid or not d:
                continue
            ref_v, ref_st = num(row[6]), status_base(row[8])
            nos = idx.get(rid, {}).get(d)
            v = nos.get("v") if nos else None
            st = nos.get("st") if nos else "fora do período"
            dif = (abs(v - ref_v) / abs(ref_v) * 100) if (v is not None and ref_v not in (None, 0)) else None
            ok_v = dif is not None and dif <= 2
            ok_s = st == ref_st
            r = resumo[(aba, ponto, rid)]
            r[0] += 1
            r[1] += ok_v
            r[2] += ok_s
            r[3] += (v is None)
            linhas.append([aba, d, ponto, rid, row[5], ref_v, v, f"{dif:.1f}" if dif is not None else "", ref_st, st])
    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["aba", "data", "ponto", "regra", "regra_planilha", "valor_planilha", "valor_painel", "dif_%", "status_planilha", "status_painel"])
        w.writerows(linhas)
    print(f"{'aba':22} {'ponto':38} {'regra':20} {'n':>3} {'valor≤2%':>8} {'status=':>7} {'sem dado':>8}")
    for (aba, ponto, rid), (n, a, b, c) in resumo.items():
        print(f"{aba:22} {ponto[:38]:38} {rid:20} {n:3} {a:8} {b:7} {c:8}")
    print(f"-> {saida}")


if __name__ == "__main__":
    main()
