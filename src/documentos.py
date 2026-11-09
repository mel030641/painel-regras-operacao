"""
documentos.py - Gera as planilhas de apoio publicadas com o painel (aba Documentos).

Saídas (pasta <site>/docs):
  acompanhamento_regras.xlsx  situação atual, conformidade mensal e ocorrências de cada regra
  historico_faixas.xlsx       faixas operativas publicadas (boletins ANA / Power BI), desde out/2017
  indicadores_ASREG.xlsx      planilha de indicadores da ASREG com o status apurado pelo painel

Uso: python src/documentos.py [painel.json] [pasta_site]
Dependência: openpyxl >= 3.1 (requirements.txt).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

RAIZ = Path(__file__).resolve().parents[1]
AVAL = ("Atende", "Tolerância", "Não atende")
GRAV = {"Não atende": 3, "Tolerância": 2, "Atende": 1}
COR = {"Atende": "DFF2E3", "Tolerância": "FBEFD2", "Não atende": "F8DEDE"}
CAB = PatternFill("solid", fgColor="1F4E5F")
NOMES_FAIXA = {"CANTAREIRA": "Sistema Equivalente Cantareira", "GRFURN": "Furnas", "GRMMOR": "M. Mascarenhas de Moraes (Peixoto)",
               "PREMBO": "T. C. Santiago (Emborcação)", "PRITUM": "Itumbiara", "PPJURU": "Jurumirim", "PPCHAV": "Chavantes",
               "PPCAPI": "Capivara", "SFTMAR": "Três Marias", "SFSOBR": "Sobradinho", "TOSMES": "Serra da Mesa"}


def _d(iso):
    return dt.date.fromisoformat(iso) if iso else None


def aba(wb: Workbook, titulo: str, colunas: list[tuple[str, int]], linhas, status_col: str | None = None, primeira=False):
    ws = wb.active if primeira else wb.create_sheet()
    ws.title = titulo
    ws.append([c for c, _ in colunas])
    for i, (_, w) in enumerate(colunas, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        cel = ws.cell(row=1, column=i)
        cel.font, cel.fill = Font(bold=True, color="FFFFFF"), CAB
        cel.alignment = Alignment(wrap_text=True, vertical="center")
    idx = [c for c, _ in colunas].index(status_col) + 1 if status_col else None
    for ln in linhas:
        ws.append(ln)
        if idx:
            cel = ws.cell(row=ws.max_row, column=idx)
            if cel.value in COR:
                cel.fill = PatternFill("solid", fgColor=COR[cel.value])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, dt.date):
                c.number_format = "DD/MM/YYYY"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def leia_me(wb, titulo, linhas):
    ws = wb.active
    ws.title = "Leia-me"
    ws.column_dimensions["A"].width = 120
    ws.append([titulo])
    ws["A1"].font = Font(bold=True, size=13)
    for t in linhas:
        ws.append([t])
        ws.cell(row=ws.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")


def conformidade(cont: Counter):
    n = sum(cont[s] for s in AVAL)
    return (round(100 * (cont["Atende"] + cont["Tolerância"]) / n, 1) if n else None), n


def efetivo(r):
    """Último registro avaliado (dias 'Em curso' trazem a situação parcial em 'parc')."""
    return r.get("ultimo") or {}


def acompanhamento(D, destino: Path):
    hoje = _d(D["periodo"][1])
    ano = str(hoje.year)
    ini12 = (hoje - dt.timedelta(days=365)).isoformat()
    wb = Workbook()
    leia_me(wb, "Acompanhamento das regras de operação - SOE/ANA", [
        f"Período apurado: {_d(D['periodo'][0]):%d/%m/%Y} a {hoje:%d/%m/%Y}. Gerado em {D['gerado_em'][:16].replace('T', ' ')} UTC a partir de painel.json.",
        "Regras: situação no último dia avaliado, conformidade no ano corrente, nos últimos 12 meses e em todo o histórico.",
        "Conformidade = (Atende + Tolerância) ÷ avaliações com resultado (Atende + Tolerância + Não atende), mesmo critério do painel. Dias Em curso, Sem dado, Não se aplica e Parâmetro pendente não entram na conta.",
        "Mensal: contagem de avaliações por regra e mês. Ocorrências: cada avaliação em Não atende ou Tolerância, com valor, limite e folga.",
        "Antes da entrada em vigor de cada resolução a regra é 'Não se aplica'. As Res. 1.382/2015 e 2.081/2017 (vigor após comunicado da ANA) são avaliadas desde 01/01/2024 até a data ser conferida.",
    ])
    linhas, mensal, ocorr = [], [], []
    for r in D["regras"]:
        serie = r.get("serie", [])
        tudo = Counter(x["st"] for x in serie)
        c_ano = Counter(x["st"] for x in serie if x["d"].startswith(ano))
        c_12 = Counter(x["st"] for x in serie if x["d"] > ini12)
        u = efetivo(r)
        p_ano, n_ano = conformidade(c_ano)
        p_12, n_12 = conformidade(c_12)
        p_t, n_t = conformidade(tudo)
        viol = [x for x in serie if x["st"] in ("Não atende", "Tolerância")]
        linhas.append([r["id"], r["sistema"], r["ponto"], r["resolucao"], r.get("artigos"), r["descricao"], r.get("unid"),
                       _d(u.get("d")), u.get("st"), u.get("parc"), u.get("v"), u.get("lim"), u.get("folga"), u.get("faixa"), u.get("orig"),
                       p_ano, n_ano, c_ano["Não atende"], c_ano["Tolerância"], p_12, p_t, n_t, tudo["Não atende"], tudo["Tolerância"],
                       _d(viol[-1]["d"]) if viol else None])
        for m, k in sorted(r.get("mensal", {}).items()):
            n = k.get("n", 0)
            mensal.append([r["id"], r["sistema"], r["ponto"], r["resolucao"], m, n, k.get("Atende", 0), k.get("Tolerância", 0),
                           k.get("Não atende", 0), round(100 * (k.get("Atende", 0) + k.get("Tolerância", 0)) / n, 1) if n else None,
                           max((s for s in AVAL if k.get(s)), key=GRAV.get, default=None)])
        for x in viol:
            ocorr.append([_d(x["d"]), r["id"], r["sistema"], r["ponto"], r["resolucao"], x["st"], x.get("v"), x.get("lim"),
                          x.get("folga"), r.get("unid"), x.get("faixa"), x.get("orig"), x.get("obs")])
    ocorr.sort(key=lambda l: (l[0], l[1]), reverse=True)
    aba(wb, "Regras", [("Código", 18), ("Sistema", 18), ("Ponto de controle", 24), ("Resolução", 22), ("Artigos", 16), ("Regra", 50),
                       ("Unidade", 8), ("Último dia avaliado", 12), ("Situação", 12), ("Parcial (janela em curso)", 12), ("Valor", 10),
                       ("Limite", 10), ("Folga", 10), ("Faixa", 18), ("Origem da faixa", 14),
                       (f"Conformidade {ano} (%)", 12), (f"Avaliações {ano}", 10), (f"Não atende {ano}", 10), (f"Tolerância {ano}", 10),
                       ("Conformidade 12 meses (%)", 12), ("Conformidade histórico (%)", 12), ("Avaliações histórico", 10),
                       ("Não atende histórico", 10), ("Tolerância histórico", 10), ("Última ocorrência", 12)], linhas, "Situação")
    aba(wb, "Mensal", [("Código", 18), ("Sistema", 18), ("Ponto de controle", 24), ("Resolução", 22), ("Mês", 9), ("Avaliações", 10),
                       ("Atende", 8), ("Tolerância", 10), ("Não atende", 10), ("Conformidade (%)", 12), ("Pior situação no mês", 14)],
        mensal, "Pior situação no mês")
    aba(wb, "Ocorrências", [("Data", 11), ("Código", 18), ("Sistema", 18), ("Ponto de controle", 24), ("Resolução", 22), ("Situação", 12),
                            ("Valor", 10), ("Limite", 10), ("Folga", 10), ("Unidade", 8), ("Faixa", 18), ("Origem da faixa", 14),
                            ("Observação", 40)], ocorr, "Situação")
    wb.save(destino)
    return len(linhas), len(ocorr)


def historico_faixas(destino: Path):
    F = json.loads((RAIZ / "config" / "faixas.json").read_text(encoding="utf-8"))
    wb = Workbook()
    leia_me(wb, "Histórico das faixas operativas publicadas", [
        f"Fonte: config/faixas.json (atualizado em {F.get('atualizado_em', '—')}). {F.get('fonte', '')}",
        "Cada linha é um período de vigência de faixa (mês, semana operativa ou período hidrológico, conforme a resolução), com o boletim diário oficial da ANA (gov.br) ou o painel Power BI da SOE que a publicou.",
        "Ordem de prioridade no painel: boletim diário da ANA, depois Power BI da SOE e, por último, a faixa calculada pela regra da resolução.",
        "Status 'confirmada': mesma faixa em todos os boletins lidos do período. 'divergente': boletins do mesmo período com faixas diferentes (prevalece a de maior recorrência).",
    ])
    linhas = []
    for chave, v in F.get("faixas", {}).items():
        rid, per = chave.split("|", 1)
        vig = v.get("vigencia") or [None, None]
        linhas.append([rid, NOMES_FAIXA.get(rid, rid), per, _d(vig[0]), _d(vig[1]), v.get("faixa"), v.get("origem"), v.get("status"),
                       v.get("vu_ref"), _d(v.get("boletim")), v.get("url")])
    linhas.sort(key=lambda l: (l[0], l[3] or dt.date.min))
    aba(wb, "Faixas", [("Código", 12), ("Reservatório / sistema", 30), ("Período", 12), ("Vigência início", 12), ("Vigência fim", 12),
                       ("Faixa", 24), ("Origem", 14), ("Status", 12), ("Referência (VU % ou nível)", 14), ("Boletim de origem", 12),
                       ("URL oficial", 70)], linhas)
    ws = wb["Faixas"]
    for row in ws.iter_rows(min_row=2, min_col=11, max_col=11):
        for c in row:
            if c.value:
                c.hyperlink = c.value
                c.font = Font(color="0563C1", underline="single")
    wb.save(destino)
    return len(linhas)


def asreg(D, destino: Path):
    A = json.loads((RAIZ / "config" / "asreg_indicadores.json").read_text(encoding="utf-8"))
    R = {r["id"]: r for r in D["regras"]}
    hoje = _d(D["periodo"][1])
    ano = str(hoje.year)
    wb = Workbook()
    leia_me(wb, "Indicadores das regras de operação de reservatórios (SOE) - formato ASREG", [
        f"Base: {A['fonte']}",
        f"As colunas à direita (fundo verde-água no cabeçalho) são apuradas pelo painel em {hoje:%d/%m/%Y}: situação do último dia avaliado e conformidade em {ano}.",
        "Quando uma linha da ASREG corresponde a mais de uma regra do painel (ex.: mínima diária e máxima mensal), a situação mostrada é a pior entre elas, e as contagens somam as regras.",
        "Conformidade = (Atende + Tolerância) ÷ avaliações com resultado, mesmo critério do painel.",
        "A coluna 'Detalhamento do Indicador (O que mede)' não foi transcrita do arquivo original e fica em branco.",
    ])
    cols = ["Resolução", "Monitorada", "Sistema Hídrico", "Reservatório / Ponto de Controle", "Formalização", "Tipo de indicador", "Indicador",
            "Detalhamento do Indicador (O que mede)", "Métrica", "Detalhamento da métrica (Como é calculado)",
            "Artigo(s) da Resolução de Referência", "Base de dados", "Data de Início", "Freq. Coleta", "Meta", "Gatilho de avaliação",
            "UORG", "Sistema", "Automatizado", "Indicador plausível?", "Forma de monitoramento"]
    extra = ["Regras do painel", "Situação (painel)", "Último dia avaliado", f"Conformidade {ano} (%)", f"Avaliações {ano}",
             f"Não atende {ano}", f"Tolerância {ano}", "Última ocorrência"]
    linhas = []
    for l in A["linhas"]:
        base = []
        for c in cols:
            v = l.get(c, "")
            if not v and l["Monitorada"] == "Sim":
                v = A["comuns"].get(c, "")
            elif not v and c in A["comuns"]:
                v = "Não aplicado" if c not in ("UORG",) else A["comuns"][c]
            base.append(v)
        regs = [R[i] for i in l["regras_painel"] if i in R]
        if regs:
            sts = [(r.get("ultimo") or {}).get("st") for r in regs]
            pior = max(sts, key=lambda s: GRAV.get(s, 0))
            if GRAV.get(pior, 0) == 0:
                pior = sts[0]
            datas = [(r.get("ultimo") or {}).get("d") for r in regs if (r.get("ultimo") or {}).get("d")]
            c = Counter(x["st"] for r in regs for x in r.get("serie", []) if x["d"].startswith(ano))
            p, n = conformidade(c)
            viol = [x["d"] for r in regs for x in r.get("serie", []) if x["st"] in ("Não atende", "Tolerância")]
            linhas.append(base + [", ".join(l["regras_painel"]), pior, _d(min(datas)) if datas else None, p, n, c["Não atende"],
                                  c["Tolerância"], _d(max(viol)) if viol else None])
        else:
            linhas.append(base + ["—", "Não monitorada", None, None, None, None, None, None])
    w = {"Resolução": 22, "Sistema Hídrico": 16, "Reservatório / Ponto de Controle": 24, "Detalhamento da métrica (Como é calculado)": 44,
         "Forma de monitoramento": 34, "Artigo(s) da Resolução de Referência": 20, "Sistema": 26, "Regras do painel": 28}
    ws = aba(wb, "Table 1", [(c, w.get(c, 13)) for c in cols + extra], linhas, "Situação (painel)")
    for i in range(len(cols) + 1, len(cols) + len(extra) + 1):
        ws.cell(row=1, column=i).fill = PatternFill("solid", fgColor="0E7C86")
    # status mensal do ano corrente por linha da ASREG
    meses = [f"{ano}-{m:02d}" for m in range(1, hoje.month + 1)]
    mens = []
    for l in A["linhas"]:
        regs = [R[i] for i in l["regras_painel"] if i in R]
        if not regs:
            continue
        ln = [l["Resolução"], l["Reservatório / Ponto de Controle"]]
        for m in meses:
            k = Counter()
            for r in regs:
                k.update({s: v for s, v in (r.get("mensal", {}).get(m) or {}).items() if s in AVAL})
            ln.append(max((s for s in AVAL if k[s]), key=GRAV.get, default="—"))
        mens.append(ln)
    ws2 = aba(wb, f"Status mensal {ano}", [("Resolução", 22), ("Reservatório / Ponto de Controle", 26)] + [(m, 11) for m in meses], mens)
    for row in ws2.iter_rows(min_row=2, min_col=3):
        for c in row:
            if c.value in COR:
                c.fill = PatternFill("solid", fgColor=COR[c.value])
    wb.save(destino)
    return len(linhas)


def main():
    painel = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "data" / "painel.json"
    site = Path(sys.argv[2]) if len(sys.argv) > 2 else RAIZ / "_site"
    D = json.loads(painel.read_text(encoding="utf-8"))
    docs = site / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    n, o = acompanhamento(D, docs / "acompanhamento_regras.xlsx")
    f = historico_faixas(docs / "historico_faixas.xlsx")
    a = asreg(D, docs / "indicadores_ASREG.xlsx")
    print(f"[ok] documentos em {docs}: acompanhamento ({n} regras, {o} ocorrências), faixas ({f} períodos), ASREG ({a} linhas)")


if __name__ == "__main__":
    main()
