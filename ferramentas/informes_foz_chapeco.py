"""
informes_foz_chapeco.py - Gera config/informes_foz_chapeco.json a partir da consolidação dos e-mails do agente
da UHE Foz do Chapecó (Res. ANA 49/2018, art. 1º, §§ 2º a 5º).

Entrada (pasta de trabalho da SOE, fora do repositório; separador ';', vírgula decimal, UTF-8 com BOM):
    emails_programacoes.csv          uma linha por e-mail (consolida_defluencia_foz_chapeco.py)
    cobertura_fins_de_semana.csv     uma linha por sexta, com os e-mails de cada fim de semana
    programacoes_todas_versoes.csv   valores horários de todas as planilhas, por e-mail

Saída: config/informes_foz_chapeco.json, só com dados derivados por fim de semana (data e hora de recebimento,
tipo do envio, caracterização declarada, máximas programadas e horas cobertas). Não leva assunto, remetente,
anexo nem hash: os e-mails ficam na pasta de trabalho da SOE.

Uso:
    python ferramentas/informes_foz_chapeco.py --pasta "<pasta com os CSVs>" [--base-fim 2026-11-19]

--base-fim: último dia coberto pela caixa de e-mails consolidada (padrão: data do último e-mail).
Sextas posteriores ficam como "Sem dado" no painel até a base ser atualizada.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "config" / "informes_foz_chapeco.json"
ENCAMINHADO = ("ENC:", "FW:", "FWD:", "TR:")  # e-mail encaminhado: o horário não é o do envio original do agente


def ler(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def num(s: str | None) -> float | None:
    s = (s or "").strip()  # vírgula decimal, sem separador de milhar
    return float(s.replace(",", ".")) if s else None


def caract(txt: str | None) -> str | None:
    t = (txt or "").lower()
    baixa, alta = "baixas vaz" in t, "altas vaz" in t
    if baixa and alta:
        return "ambas"  # planilha com as duas marcações; tratada como não caracterizada
    return "baixas" if baixa else ("altas" if alta else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", required=True, help="pasta com os CSVs da consolidação dos e-mails")
    ap.add_argument("--base-fim", help="último dia coberto pela base de e-mails (aaaa-mm-dd)")
    ap.add_argument("--saida", default=str(SAIDA))
    a = ap.parse_args()
    pasta = Path(a.pasta)

    emails = {e["email_origem"]: e for e in ler(pasta / "emails_programacoes.csv")}
    cobertura = ler(pasta / "cobertura_fins_de_semana.csv")
    horas: dict[str, dict[str, float]] = defaultdict(dict)
    for r in ler(pasta / "programacoes_todas_versoes.csv"):
        if r.get("versao_valida", "sim") != "não" and num(r["vazao_m3s"]) is not None:
            horas[r["email_origem"]][r["data_hora"]] = num(r["vazao_m3s"])

    semanas, ultimo = {}, None
    for c in cobertura:
        sexta = dt.date.fromisoformat(c["sexta_referencia"])
        ini, meio, fim = (dt.datetime.combine(sexta, dt.time(0)), dt.datetime.combine(sexta, dt.time(12)),
                          dt.datetime.combine(sexta + dt.timedelta(days=2), dt.time(12)))
        envios = []
        for eid in [x for x in (c.get("emails") or "").split("|") if x]:
            e = emails[eid]
            receb = dt.datetime.strptime(e["recebido_em"], "%d/%m/%Y %H:%M")
            ultimo = max(ultimo or receb, receb)
            if e.get("versao_valida") == "não":
                continue  # planilha descartada na consolidação (ver relatorio_inconsistencias.md)
            cls = e.get("classificacao") or ""
            tipo = "cancelamento" if cls.startswith("cancelamento") else ("sem_planilha" if cls == "aviso sem planilha" or not horas.get(eid) else "programacao")
            h = horas.get(eid, {})
            jan_toda = [v for k, v in h.items() if ini <= dt.datetime.strptime(k, "%Y-%m-%d %H:%M") <= fim]
            jan_caput = [v for k, v in h.items() if meio <= dt.datetime.strptime(k, "%Y-%m-%d %H:%M") <= fim]
            envios.append({
                "recebido": receb.isoformat(timespec="minutes"),
                "tipo": tipo,
                "encaminhado": (e.get("assunto") or "").strip().upper().startswith(ENCAMINHADO),
                "caract": caract(e.get("caracteristica_periodo")),
                "horas": len(jan_toda),
                "max_0h_12h": max(jan_toda) if jan_toda else None,
                "max_12h_12h": max(jan_caput) if jan_caput else None,
            })
        envios.sort(key=lambda x: x["recebido"])
        semanas[sexta.isoformat()] = envios

    base_ini = min(semanas)
    base_fim = a.base_fim or (ultimo.date().isoformat() if ultimo else None)
    saida = {
        "descricao": ("Informes semanais do agente da UHE Foz do Chapecó (Res. ANA 49/2018, art. 1º, §§ 2º a 5º), um registro por sexta. "
                      "Dados derivados dos e-mails recebidos pela SOE e consolidados fora do repositório; gerado por "
                      "ferramentas/informes_foz_chapeco.py. 'horas' = horas programadas de 0h de sexta a 12h de domingo (61 no informe completo); "
                      "'max_0h_12h' = vazão defluente máxima programada nesse período; 'max_12h_12h' = máxima de 12h de sexta a 12h de domingo; "
                      "'encaminhado' = e-mail recebido por encaminhamento, cujo horário não é o do envio original do agente."),
        "fonte": "E-mails do agente à ANA (caixa da SOE), consolidados pela SOE; documentos fora do repositório",
        "base_inicio": base_ini,
        "base_fim": base_fim,
        "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "semanas": semanas,
    }
    Path(a.saida).write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    n = defaultdict(int)
    for v in semanas.values():
        n["com e-mail" if v else "sem e-mail"] += 1
    print(f"[ok] {len(semanas)} sextas ({dict(n)}), base {base_ini} a {base_fim} -> {a.saida}")


if __name__ == "__main__":
    sys.exit(main())
