"""
faixas_powerbi.py - Lê as faixas operativas publicadas nos painéis Power BI da SOE e grava config/faixas.json.

Fonte: painel índice "Links dos Painéis SOE" (ANA/SOE/COSH), publicado em
https://app.powerbi.com/view?r=eyJrIjoiMDlkNDFhZjYtYjRjNC00NjA1LTg4NzUtNWQ2OTRiZWM2Y2FkIiwidCI6ImUwYmI0MDEyLTgxMGItNDY5YS04YjRkLTY2N2ZjZDFiYWY4OCJ9
e os painéis de cada sistema listados nele.

Método: API pública do "publish to web" (cluster wabi-brazil-south-d-primary-api):
  GET  /public/reports/{chave}/modelsAndExploration   -> modelo e data de atualização
  POST /public/reports/conceptualschema                -> tabelas e colunas
  POST /public/reports/querydata?synchronous=true      -> consulta semântica (DAX gerado pelo serviço)
As faixas são lidas das próprias medidas/tabelas que os painéis exibem; nada é recalculado aqui.

Saída: config/faixas.json -> {"faixas": {"<rid>|<período>": {...}}}. O período segue a definição de cada norma
(mês 'aaaa-mm' ou sábado de início da semana operativa, Paranapanema). Entradas anteriores são preservadas,
formando o histórico das faixas publicadas; período já registrado a partir do boletim diário da ANA não é sobrescrito.

Saída opcional para conferência (--csv PASTA; CSV UTF-8 com BOM, separador ';', decimal ','):
  PASTA/paineis_soe_links.csv
  PASTA/faixas_operativas_vigentes.csv

Uso:
  python src/faixas_powerbi.py [--csv PASTA] [config/faixas.json]
  python src/faixas_powerbi.py --de-csv faixas_operativas_vigentes.csv [config/faixas.json]   (só converte um CSV já extraído)

Dependências: Python >= 3.9, somente biblioteca padrão.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regras import FAIXAS  # noqa: E402

# ---------------------------------------------------------------- parâmetros
URL_INDICE = ("https://app.powerbi.com/view?r=eyJrIjoiMDlkNDFhZjYtYjRjNC00NjA1LTg4NzUtNWQ2OTRiZWM2Y2FkIiwi"
              "dCI6ImUwYmI0MDEyLTgxMGItNDY5YS04YjRkLTY2N2ZjZDFiYWY4OCJ9")
HOST_API = "https://wabi-brazil-south-d-primary-api.analysis.windows.net"
TABELA_LINKS = "Links Power BI"          # tabela do painel índice com Title/Url
DESTINO_PADRAO = Path(__file__).resolve().parents[1] / "config" / "faixas.json"
TIMEOUT_S = 90
TENTATIVAS = 3
LIMITE_LINHAS = 5000
CAMPOS_LINKS = ["painel", "url", "chave", "modelo_id", "ultima_atualizacao_modelo", "n_tabelas", "tem_faixa_operativa",
                "status_api", "data_extracao"]
CAMPOS_FAIXAS = ["sistema", "reservatorio", "faixa_vigente", "periodo_hidrologico", "vu_referencia_pct", "data_referencia",
                 "condicao_restricao", "limites", "texto_painel", "data_boletim", "ultima_atualizacao_modelo", "painel",
                 "url_painel", "tabela_origem", "data_extracao"]


# ---------------------------------------------------------------- cliente HTTP
def _req(path, chave, corpo=None):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    cab = {"X-PowerBI-ResourceKey": chave, "Content-Type": "application/json", "Accept": "application/json"}
    ultimo = None
    for n in range(TENTATIVAS):
        try:
            rq = urllib.request.Request(HOST_API + path, data=dados, headers=cab)
            with urllib.request.urlopen(rq, timeout=TIMEOUT_S) as r:
                bruto = r.read()
            if bruto[:2] == b"\x1f\x8b":  # alguns painéis respondem em gzip mesmo sem Accept-Encoding
                bruto = gzip.decompress(bruto)
            return json.loads(bruto.decode("utf-8"))
        except urllib.error.HTTPError as e:
            ultimo = RuntimeError(f"HTTP {e.code} em {path}: {e.read()[:300]!r}")
            if e.code in (401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            ultimo = RuntimeError(f"Falha de rede em {path}: {e}")
        time.sleep(2 ** (n + 1))
    raise ultimo


def chave_do_link(url):
    r = url.split("r=", 1)[1].split("&", 1)[0]
    r += "=" * (-len(r) % 4)
    return json.loads(base64.b64decode(r))["k"]


class Painel:
    def __init__(self, titulo, url):
        self.titulo, self.url = titulo, url
        self.chave = chave_do_link(url)
        m = _req(f"/public/reports/{self.chave}/modelsAndExploration?preferReadOnlySession=true", self.chave)
        self.modelo = m["models"][0]["id"]
        self.atualizado = m["models"][0].get("LastRefreshTime", "")
        s = _req("/public/reports/conceptualschema", self.chave, {"modelIds": [self.modelo]})
        self.tabelas = {e["Name"]: [p["Name"] for p in e["Properties"]] for e in s["schemas"][0]["schema"]["Entities"]}

    def consulta(self, tabela, colunas=(), medidas=()):
        """Retorna lista de linhas (colunas agrupadas + medidas avaliadas no contexto padrão do painel)."""
        sel = [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": c}, "Name": f"c{i}"} for i, c in enumerate(colunas)]
        sel += [{"Measure": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": m}, "Name": f"m{i}"} for i, m in enumerate(medidas)]
        n = len(sel)
        corpo = {"version": "1.0.0", "cancelQueries": [], "modelId": self.modelo, "queries": [{"QueryId": "", "Query": {"Commands": [{
            "SemanticQueryDataShapeCommand": {
                "Query": {"Version": 2, "From": [{"Name": "t", "Entity": tabela, "Type": 0}], "Select": sel},
                "Binding": {"Primary": {"Groupings": [{"Projections": list(range(n))}]},
                            "DataReduction": {"DataVolume": 4, "Primary": {"Window": {"Count": LIMITE_LINHAS}}}, "Version": 1}}}]}}]}
        d = _req("/public/reports/querydata?synchronous=true", self.chave, corpo)
        res = d["results"][0]["result"]["data"]
        try:
            ds = res["dsr"]["DS"][0]
        except (KeyError, IndexError):
            raise RuntimeError(f"Consulta rejeitada ({self.titulo}/{tabela}): {json.dumps(res, ensure_ascii=False)[:400]}")
        if "odata.error" in json.dumps(ds):
            raise RuntimeError(f"Erro DAX ({self.titulo}/{tabela}): {json.dumps(ds, ensure_ascii=False)[:400]}")
        dic, linhas, ant, S = ds.get("ValueDicts", {}), [], [None] * n, None
        for ph in ds.get("PH", []):
            for dms in ph.values():
                for dm in dms:
                    S = dm.get("S", S)
                    nomes = [x["N"] for x in (S or [])]
                    vals = dm["C"] if "C" in dm else [dm[k] for k in nomes if k in dm]
                    R, nul, it, lin = dm.get("R", 0), dm.get("Ø", 0), iter(vals), []
                    for j in range(n):
                        if R >> j & 1:
                            lin.append(ant[j])
                        elif nul >> j & 1:
                            lin.append(None)
                        else:
                            lin.append(next(it, None))
                    ant = lin
                    linhas.append(lin)
        for j, s in enumerate((S or [])[:n]):
            if "DN" in s:
                for lin in linhas:
                    if isinstance(lin[j], int):
                        lin[j] = dic[s["DN"]][lin[j]]
        return linhas

    def max_data(self, tabela, coluna):
        v = [r[0] for r in self.consulta(tabela, (coluna,)) if isinstance(r[0], (int, float))]
        return ms_para_data(max(v)) if v else ""


def ms_para_data(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()


def limpa(v):
    if isinstance(v, str):
        return v.strip().strip("'")
    return v


# ---------------------------------------------------------------- extratores por sistema
def reg(sistema, reservatorio, faixa, **kw):
    base = {"sistema": sistema, "reservatorio": reservatorio, "faixa_vigente": limpa(faixa), "periodo_hidrologico": "",
            "vu_referencia_pct": "", "data_referencia": "", "condicao_restricao": "", "limites": "", "texto_painel": "",
            "data_boletim": "", "painel": "", "url_painel": "", "tabela_origem": ""}
    base.update({k: limpa(v) for k, v in kw.items()})
    return base


def ext_cantareira(p):
    f, txt, per, eesi = p.consulta("55_Cantareira", (), (".Faixa Dbol", ".Faixa DBol Texto", ".Periodo DBol", ".EESI Autorizado"))[0]
    m = re.search(r"em (\d{1,2} de \w+ de \d{4}), ([\d,]+)% de seu volume útil.*?no mês de (\w+ de \d{4})", txt or "")
    return [reg("Cantareira", "Sistema Equivalente Cantareira", f, periodo_hidrologico=per,
                vu_referencia_pct=m.group(2) if m else "", data_referencia=m.group(1) if m else "",
                limites=f"Retirada autorizada EESI (Santa Inês) = {eesi} m³/s; mês de aplicação: {m.group(3) if m else ''}",
                texto_painel=txt, data_boletim=p.max_data("55_Cantareira", "med_data_medicao"),
                tabela_origem="55_Cantareira [.Faixa Dbol, .Faixa DBol Texto, .Periodo DBol, .EESI Autorizado]")]


def ext_sao_francisco(p):
    db = p.max_data("Data Boletim", "Data Boletim")
    out = []
    f, vu, sub = p.consulta("Tres Marias InicMes", (), ("Faixa 3M", "VU 3M do Mes", "SubT 3M"))[0]
    lin = max(p.consulta("Tres Marias InicMes", ("med_data_medicao", "Vazão Máxima", "Qmin")), key=lambda r: r[0] or 0)
    out.append(reg("São Francisco", "Três Marias", f, vu_referencia_pct=vu, data_referencia=sub,
                   limites=f"Defluência máxima = {lin[1]}; defluência mínima = {lin[2]} m³/s (mês iniciado em {ms_para_data(lin[0])})",
                   data_boletim=db, tabela_origem="Tres Marias InicMes [Faixa 3M, VU 3M do Mes, SubT 3M, Vazão Máxima, Qmin]"))
    f, vu, sub = p.consulta("Sobradinho InicMes", (), ("Faixa SB", "VU SB do Mes", "SubT SB"))[0]
    lin = max(p.consulta("Sobradinho InicMes", ("med_data_medicao", "Qmáx mensal Xingó", "Qmin Sobradinho", "Qmin Xingó")), key=lambda r: r[0] or 0)
    per = [r[1] for r in p.consulta("FAIXA_Qmáx_Mín_Sobradinho", ("Mês", "PERÍODO")) if r[0] == lin[0]]
    out.append(reg("São Francisco", "Sobradinho", f, vu_referencia_pct=vu, data_referencia=sub, periodo_hidrologico=per[0] if per else "",
                   limites=f"Qmáx mensal Xingó = {lin[1]}; defluência mínima Sobradinho = {lin[2]} m³/s; defluência mínima Xingó = {lin[3]} m³/s",
                   data_boletim=db, tabela_origem="Sobradinho InicMes [Faixa SB, VU SB do Mes, SubT SB] + FAIXA_Qmáx_Mín_Sobradinho"))
    return out


def ext_paranapanema(p):
    db = p.max_data("Data Boletim", "Data Boletim")
    out = []
    for nome, tab, pre in (("Jurumirim", "Jurumirim- Gráfico", "Jur"), ("Chavantes", "Chavantes - Gráfico", "Ch"), ("Capivara", "Capivara - Gráfico", "Cap")):
        f, nivel, aten, alerta, restr, maxima = p.consulta(tab, (), tuple(f"{pre}_{x}" for x in ("Faixa", "Nivel", "Atenção", "Alerta", "Restrição", "Máxima")))[0]
        out.append(reg("Paranapanema", nome, f, condicao_restricao=maxima,
                       limites=f"Nível atual = {nivel} m; limiar Atenção = {aten} m; Alerta = {alerta} m; Restrição = {restr} m",
                       data_boletim=db, tabela_origem=f"{tab} [{pre}_Faixa, {pre}_Nivel, {pre}_Atenção, {pre}_Alerta, {pre}_Restrição, {pre}_Máxima]"))
    return out


def ext_tocantins(p):
    db = p.max_data("Data Boletim", "Data Boletim")
    per, lim_sm, lim_estr = p.consulta("Operação", (), ("Periodo", "Limite SM", "Limite ESTR"))[0]
    out = []
    for res, f, vu, qmax in p.consulta("Dados 1Dia mes", ("Reservatorio.res_nome",), ("Faixa", "VU do mes", "Vazao maxima")):
        out.append(reg("Tocantins", res.title(), f, periodo_hidrologico=per, vu_referencia_pct=vu, condicao_restricao=qmax,
                       limites=f"Vazão mínima média diária Serra da Mesa = {lim_sm}; vazão mínima média diária Estreito = {lim_estr}",
                       data_boletim=db, tabela_origem="Dados 1Dia mes [Faixa, VU do mes, Vazao maxima] + Operação [Periodo, Limite SM, Limite ESTR]"))
    return out


def ext_grande_paranaiba(p):
    db = p.max_data("Dados_5dias", "med_data_medicao")
    out = []
    cols = ("res_nome", "nome_faixa", "nome_período", "vol_util referencia", "Condição", "Condição Art9")
    for res, f, per, vu, cond, art9 in p.consulta("Faixas_Mes", cols):
        out.append(reg("Grande e Paranaíba", res.title(), f, periodo_hidrologico=per, vu_referencia_pct=vu,
                       condicao_restricao=cond, limites=f"Condição Art. 9: {art9}", data_boletim=db,
                       tabela_origem="Faixas_Mes [nome_faixa, nome_período, vol_util referencia, Condição, Condição Art9]"))
    return out


EXTRATORES = {"Cantareira": ext_cantareira, "São Francisco": ext_sao_francisco, "Paranapanema": ext_paranapanema,
              "Tocantins": ext_tocantins, "Grande e Paranaíba": ext_grande_paranaiba}


# ---------------------------------------------------------------- extração
def extrair():
    """Lê o painel índice e os painéis com faixa operativa. Retorna (registros de links, registros de faixas, erros)."""
    agora = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    indice = Painel("Painel links", URL_INDICE)
    links = indice.consulta(TABELA_LINKS, ("Title", "Url"))
    print(f"Índice: {len(links)} painéis listados (modelo {indice.modelo}).")
    reg_links, faixas, erros = [], [], []
    for titulo, url in links:
        info = {"painel": titulo, "url": url, "data_extracao": agora}
        try:
            p = Painel(titulo, url)
            info.update(chave=p.chave, modelo_id=p.modelo, ultima_atualizacao_modelo=p.atualizado,
                        n_tabelas=len(p.tabelas), status_api="OK",
                        tem_faixa_operativa="sim" if titulo in EXTRATORES else "não")
            if titulo in EXTRATORES:
                for r in EXTRATORES[titulo](p):
                    r.update(painel=titulo, url_painel=url, ultima_atualizacao_modelo=p.atualizado, data_extracao=agora)
                    faixas.append(r)
        except Exception as e:  # registra e segue
            info.update(status_api=f"ERRO: {e}"[:300])
            erros.append((titulo, str(e)))
        reg_links.append(info)
        print(f"  {titulo}: {info['status_api'][:80]}")
    return reg_links, faixas, erros


def fmt(v):
    """Formato texto do CSV (decimal com vírgula, 2 casas). Usado também antes da conversão, para que o
    faixas.json seja o mesmo gravando ou não o CSV."""
    if isinstance(v, float):
        return f"{v:.2f}".replace(".", ",")
    return "" if v is None else v


def como_texto(linhas, campos):
    return [{k: fmt(l.get(k)) for k in campos} for l in linhas]


def grava_csv(caminho, linhas, campos):
    with open(caminho, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        w.writeheader()
        w.writerows(como_texto(linhas, campos))


def le_csv(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ---------------------------------------------------------------- conversão para config/faixas.json
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


def gravar_faixas(linhas, destino: Path):
    """Acrescenta ao histórico de destino as faixas extraídas (registros com campos em texto, como no CSV)."""
    atual = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    faixas = atual.get("faixas", {})
    for lin in linhas:
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


# ---------------------------------------------------------------- execução
def main():
    ap = argparse.ArgumentParser(description="Faixas operativas dos painéis Power BI da SOE -> config/faixas.json")
    ap.add_argument("destino", nargs="?", default=str(DESTINO_PADRAO), help="arquivo faixas.json (padrão: config/faixas.json)")
    ap.add_argument("--csv", metavar="PASTA", help="grava também os CSV de conferência nesta pasta")
    ap.add_argument("--de-csv", metavar="ARQUIVO", help="não acessa o Power BI: converte um faixas_operativas_vigentes.csv já extraído")
    a = ap.parse_args()
    destino = Path(a.destino)
    if a.de_csv:
        gravar_faixas(le_csv(a.de_csv), destino)
        return 0
    reg_links, faixas, erros = extrair()
    if a.csv:
        os.makedirs(a.csv, exist_ok=True)
        grava_csv(os.path.join(a.csv, "paineis_soe_links.csv"), reg_links, CAMPOS_LINKS)
        grava_csv(os.path.join(a.csv, "faixas_operativas_vigentes.csv"), faixas, CAMPOS_FAIXAS)
    print(f"Faixas extraídas: {len(faixas)} registros. Erros: {len(erros)}.")
    for t, e in erros:
        print(f"  ERRO {t}: {e[:200]}")
    if not faixas:  # falha só quando nenhuma faixa foi extraída; erro em painel isolado é só registrado
        return 1
    gravar_faixas(como_texto(faixas, CAMPOS_FAIXAS), destino)
    return 0


if __name__ == "__main__":
    sys.exit(main())
