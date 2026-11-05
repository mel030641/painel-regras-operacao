"""
boletins_ana.py - Coleta diária das faixas de operação nos boletins diários oficiais da ANA
(Sala de Situação, https://www.gov.br/ana/pt-br/sala-de-situacao/todos-os-boletins-diarios).

Ordem de leitura da faixa (decisão SOE de 05/11/2026):
  1. boletim diário oficial da ANA (este script, "origem": "Boletim ANA");
  2. painéis Power BI da SOE (faixas_powerbi.py; não sobrescreve período com boletim);
  3. faixa calculada pela regra da resolução (calculo.py), também usada como conferência.

Para cada sistema (Cantareira, São Francisco, Paranapanema, Tocantins, Grande e Paranaíba), lê a primeira página da
coleção no gov.br, escolhe o boletim mais próximo do meio do período vigente (dia 15 nos mensais; quarta-feira no
Paranapanema, semanal de sábado a sexta) e só baixa o PDF quando ele está mais perto do meio do período do que o boletim
já gravado em config/faixas.json. O PDF é convertido com o pdftotext (Poppler) e lido por boletins_parsers.py.

Controle do atraso de atualização (boletins do início do período ainda trazem a faixa anterior):
  - Cantareira, São Francisco e Tocantins: o período vem do próprio texto do boletim ("no mês de ...",
    "Estabelecida em ...", "mês/ano");
  - Paranapanema e Grande e Paranaíba: o período vem da data de publicação, e só são aceitos boletins a partir de
    DIA_MIN_SEMANAL (segunda-feira) e DIA_MIN_MENSAL do mês. Antes disso vale o Power BI ou a faixa calculada.

Uso: python src/boletins_ana.py [config/faixas.json] [--data AAAA-MM-DD]
Dependências: Python 3.10+ (biblioteca padrão) e pdftotext (pacote poppler-utils).
Saída: código 0 se ao menos um sistema foi lido sem erro; 1 caso contrário (o workflow segue com o Power BI).
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boletins_parsers as P  # noqa: E402
from regras import FAIXAS  # noqa: E402

# ------------------------------------------------------------------ parâmetros
BASE = "https://www.gov.br/ana/pt-br/sala-de-situacao/"
COLECOES = {  # código do boletim: (nome, caminho da coleção no gov.br)
    "02": ("Cantareira", "sistema-cantareira/colecao-para-boletim-diario-do-sistema-cantareira"),
    "12": ("São Francisco", "sao-francisco/colecao-para-boletim-diario-do-sao-francisco"),
    "08": ("Paranapanema", "paranapanema/colecao-para-boletim-diario-paranapanema"),
    "13": ("Tocantins", "tocantins/colecao-para-boletim-diario-do-tocantins"),
    "14": ("Grande e Paranaíba", "sistemas-hidricos-dos-rios-grande-e-paranaiba/colecao-de-boletins"),
}
SEMANAIS = {"08"}                 # Paranapanema: vigência semanal, de sábado a sexta (Res. ANA 132/2022)
PERIODO_PELA_DATA = {"08", "14"}  # período inferido da data de publicação (sujeito a atraso no início do período)
DIA_MIN_MENSAL = 4                # Grande e Paranaíba: aceita boletim a partir do dia 4 do mês
DIA_MIN_SEMANAL = 0               # Paranapanema: aceita a partir de segunda-feira (weekday 0)
RIDS_SEMANAIS = {"PPJURU", "PPCHAV", "PPCAPI"}
TIMEOUT, TENTATIVAS, PAUSA = 120, 4, 3.0
UA = "Mozilla/5.0 (painel-regras-operacao; coleta de boletins)"
PAD = re.compile(r'href="(https://www\.gov\.br/ana/pt-br/sala-de-situacao/[^"]+\.pdf)/view"[^>]*>\s*(\d{2})\.(\d{2})\.(\d{4})')


def baixar(url: str) -> bytes:
    erro = None
    for t in range(TENTATIVAS):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - registra e tenta de novo
            erro = e
            time.sleep(PAUSA * 2 ** t)
    raise RuntimeError(f"falha ao baixar {url}: {erro}")


def periodo_pub(cod: str, d: dt.date) -> str:
    if cod in SEMANAIS:
        return (d - dt.timedelta(days=(d.weekday() - 5) % 7)).isoformat()
    return d.strftime("%Y-%m")


def meio(rid: str, per: str) -> dt.date:
    if rid in RIDS_SEMANAIS:
        return dt.date.fromisoformat(per) + dt.timedelta(days=4)  # quarta-feira
    a, m = map(int, per.split("-"))
    return dt.date(a, m, 15)


def vigencia(rid: str, per: str) -> list[str]:
    if rid in RIDS_SEMANAIS:
        ini = dt.date.fromisoformat(per)
        return [ini.isoformat(), (ini + dt.timedelta(days=6)).isoformat()]
    a, m = map(int, per.split("-"))
    return [dt.date(a, m, 1).isoformat(), dt.date(a, m, calendar.monthrange(a, m)[1]).isoformat()]


def dist(rid: str, per: str, d: str | dt.date) -> int:
    d = dt.date.fromisoformat(d) if isinstance(d, str) else d
    return abs((d - meio(rid, per)).days)


def aceito_pela_data(cod: str, d: dt.date) -> bool:
    if cod not in PERIODO_PELA_DATA:
        return True
    if cod in SEMANAIS:  # sábado (5) e domingo (6) ficam de fora; segunda a sexta entram
        return d.weekday() < 5 and d.weekday() >= DIA_MIN_SEMANAL
    return d.day >= DIA_MIN_MENSAL


def listar(cod: str, hoje: dt.date) -> list[dict]:
    html = baixar(f"{BASE}{COLECOES[cod][1]}").decode("utf-8", errors="ignore")
    itens = {}
    for url, d, m, a in PAD.findall(html):
        data = dt.date(int(a), int(m), int(d))
        if data <= hoje:
            itens[url] = {"codigo": cod, "data_publicacao": data.isoformat(), "url": url}
    if not itens:
        raise RuntimeError("nenhum boletim encontrado na coleção (layout da página mudou?)")
    return sorted(itens.values(), key=lambda r: r["data_publicacao"], reverse=True)


def candidatos(cod: str, itens: list[dict], hoje: dt.date) -> list[dict]:
    """Boletim mais próximo do meio do período vigente e, se diferente, o mais recente (troca de período)."""
    per = periodo_pub(cod, hoje)
    rid_ref = "PPJURU" if cod in SEMANAIS else "SFTMAR"  # só para o cálculo do meio (semanal × mensal)
    do_per = [r for r in itens if periodo_pub(cod, dt.date.fromisoformat(r["data_publicacao"])) == per
              and aceito_pela_data(cod, dt.date.fromisoformat(r["data_publicacao"]))]
    out = []
    if do_per:
        out.append(min(do_per, key=lambda r: dist(rid_ref, per, r["data_publicacao"])))
    if cod not in PERIODO_PELA_DATA and itens[0] not in out:
        out.append(itens[0])  # texto do boletim mais recente pode anunciar a faixa do período que começa
    return out


def converter(pdf: Path, base: Path, cod: str):
    subprocess.run(["pdftotext", "-layout", str(pdf), f"{base}.txt"], check=True, timeout=300)
    subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox", str(pdf), f"{base}.p1.html"], check=True, timeout=300)
    if cod in {"08", "13", "14"}:
        subprocess.run(["pdftotext", "-bbox", str(pdf), f"{base}.bbox.html"], check=True, timeout=300)


def ler(cod: str, item: dict, tmp: Path) -> list[dict]:
    base = tmp / f"{cod}_{item['data_publicacao']}"
    conteudo = baixar(item["url"])
    if not conteudo.startswith(b"%PDF"):
        raise RuntimeError(f"resposta não é PDF: {item['url']}")
    pdf = base.with_suffix(".pdf")
    pdf.write_bytes(conteudo)
    converter(pdf, base, cod)
    d = dt.date.fromisoformat(item["data_publicacao"])
    meta = dict(item, periodo=periodo_pub(cod, d))
    return P.PARSERS[cod][1](str(base), meta)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    hoje = dt.date.today()
    if "--data" in sys.argv:
        hoje = dt.date.fromisoformat(sys.argv[sys.argv.index("--data") + 1])
        args = [a for a in args if a != hoje.isoformat()]
    destino = Path(args[0] if args else Path(__file__).resolve().parents[1] / "config" / "faixas.json")
    if not shutil.which("pdftotext"):
        print("pdftotext não encontrado (instale poppler-utils); coleta de boletins não executada.")
        return 1
    atual = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    faixas = atual.get("faixas", {})
    n_ok, gravadas, avisos = 0, [], []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for cod, (nome, _) in COLECOES.items():
            try:
                itens = listar(cod, hoje)
                for item in candidatos(cod, itens, hoje):
                    # período provável (pela data) e boletim já gravado: evita baixar PDF que não melhora a leitura
                    per_prov = periodo_pub(cod, dt.date.fromisoformat(item["data_publicacao"]))
                    rids = [r for r in FAIXAS if r in P.FAIXAS_NOMES and _rid_do_cod(r) == cod]
                    if cod in PERIODO_PELA_DATA and rids and all(
                            faixas.get(f"{r}|{per_prov}", {}).get("origem") == "Boletim ANA"
                            and dist(r, per_prov, faixas[f"{r}|{per_prov}"]["boletim"]) <= dist(r, per_prov, item["data_publicacao"])
                            for r in rids):
                        continue
                    for lin in ler(cod, item, tmp):
                        if lin.get("status_extracao") != "ok":
                            avisos.append(f"{nome} {item['data_publicacao']}: {lin.get('rid', '?')} {lin.get('status_extracao')}")
                            continue
                        rid, per, fx = lin["rid"], lin["periodo_aplicacao"], lin["faixa_publicada"]
                        if rid not in FAIXAS or fx not in FAIXAS[rid]["nomes"]:
                            avisos.append(f"{nome} {item['data_publicacao']}: faixa '{fx}' fora da lista de {rid}")
                            continue
                        k = f"{rid}|{per}"
                        ant = faixas.get(k)
                        if (ant and ant.get("origem") == "Boletim ANA" and ant.get("boletim")
                                and dist(rid, per, ant["boletim"]) <= dist(rid, per, item["data_publicacao"])):
                            if ant.get("faixa") != fx:
                                avisos.append(f"{k}: boletim de {item['data_publicacao']} traz '{fx}', mantido '{ant['faixa']}' "
                                              f"(boletim de {ant['boletim']}, mais perto do meio do período)")
                            continue
                        vu = lin.get("valor_referencia")
                        faixas[k] = {"faixa": fx, "idx": FAIXAS[rid]["nomes"].index(fx), "origem": "Boletim ANA",
                                     "texto_painel": (lin.get("texto_original") or "")[:200] or None,
                                     "periodo_hidrologico": None, "vu_ref": float(vu) if vu not in (None, "") else None,
                                     "limites": lin.get("condicao_limites") or None, "condicao": None,
                                     "boletim": item["data_publicacao"], "url": item["url"],
                                     "status": "coleta diária", "vigencia": vigencia(rid, per)}
                        gravadas.append(f"{k} = {fx} (boletim de {item['data_publicacao']})")
                print(f"  {nome}: OK")
                n_ok += 1
            except Exception as e:  # noqa: BLE001 - um sistema com erro não impede os demais
                print(f"  {nome}: ERRO {e}")
            time.sleep(1)
    atual["faixas"] = dict(sorted(faixas.items()))
    atual["fonte"] = "Boletins diários ANA (gov.br) e painéis Power BI SOE/ANA"
    atual["atualizado_em"] = dt.datetime.now().isoformat(timespec="seconds")
    destino.write_text(json.dumps(atual, ensure_ascii=False, indent=1), encoding="utf-8")
    for g in gravadas:
        print("  gravada:", g)
    for a in avisos:
        print("  aviso:", a)
    print(f"Boletins ANA: {n_ok}/{len(COLECOES)} sistemas lidos; {len(gravadas)} faixas gravadas em {destino}.")
    return 0 if n_ok else 1


def _rid_do_cod(rid: str) -> str:
    return {"CANTAREIRA": "02", "SFTMAR": "12", "SFSOBR": "12", "PPJURU": "08", "PPCHAV": "08", "PPCAPI": "08",
            "TOSMES": "13", "GRFURN": "14", "GRMMOR": "14", "PREMBO": "14", "PRITUM": "14"}.get(rid, "")


if __name__ == "__main__":
    sys.exit(main())
