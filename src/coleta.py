"""
coleta.py - Coletores das fontes primárias usadas pelo painel de monitoramento das normas.

Fontes:
  * ONS Dados Abertos (S3): dados hidrológicos diários (DADOS_HIDROLOGICOS_RES_<ano>.csv)
    e horários (DADOS_HIDROLOGICOS_HO_<ano>_<mm>.csv) por reservatório.
  * ANA SAR (raspagem HTML): Sistema Cantareira (MedicaoCantareira).
  * ANA Telemetria (ServiceANA.asmx): estações de controle (opcional, conferência).
  * Sabesp, Portal dos Mananciais (API REST v4): Elevatória Santa Inês e, como reserva do SAR,
    reservatórios do Cantareira. Dia da API = média de D-1 07:00 a D 07:00.

Somente biblioteca padrão do Python 3.9+. Respeita HTTPS_PROXY / SSL_CERT_FILE do ambiente.
Os arquivos baixados ficam em cache em data/cache/ (o ano/mês corrente é sempre baixado de novo).
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ----------------------------------------------------------------------------- parâmetros
RAIZ = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("PAINEL_CACHE", RAIZ / "data" / "cache"))
ONS_DIARIO = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/dados_hidrologicos_di/DADOS_HIDROLOGICOS_RES_{ano}.csv"
ONS_HORARIO = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/dados_hidrologicos_ho/DADOS_HIDROLOGICOS_HO_{ano}_{mes:02d}.csv"
SAR_CANTAREIRA = "https://www.ana.gov.br/sar0/MedicaoCantareira"
TELEMETRIA = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
SABESP_API = "https://mananciais.sabesp.com.br/api/v4"  # Swagger: https://mananciais.sabesp.com.br/swagger/index.html
TIMEOUT = 120
TENTATIVAS = 4
USER_AGENT = "painel-normas-soe/1.0 (python-urllib)"


# ----------------------------------------------------------------------------- utilitários
def _contexto_ssl() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def baixar(url: str) -> bytes:
    """GET com repetição e espera crescente. Levanta RuntimeError após TENTATIVAS falhas."""
    ultimo = None
    for i in range(TENTATIVAS):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_contexto_ssl()) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise RuntimeError(f"HTTP {e.code} em {url}") from e
            ultimo = e
        except Exception as e:  # timeout, reset etc.
            ultimo = e
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"Falha ao baixar {url}: {ultimo}")


def _num(txt: str | None) -> float | None:
    if txt is None:
        return None
    t = str(txt).strip()
    if t == "" or t.lower() in ("nan", "null", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        t = t.replace(".", "").replace(",", ".")  # padrão brasileiro
        try:
            return float(t)
        except ValueError:
            return None


def _cache(nome: str, url: str, renovar: bool) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    arq = CACHE / nome
    if arq.exists() and not renovar:
        return arq.read_bytes()
    dados = baixar(url)
    arq.write_bytes(dados)
    return dados


# ----------------------------------------------------------------------------- ONS
CAMPOS_ONS = {
    "nivel": "val_nivelmontante",
    "vu": ("val_volumeutilcon", "val_volumeutil"),
    "afl": "val_vazaoafluente",
    "defl": "val_vazaodefluente",
    "transf": "val_vazaotransferida",
    "turb": "val_vazaoturbinada",
    "vert": "val_vazaovertida",
    "outras": "val_vazaooutrasestruturas",
}


def _linha_ons(row: dict) -> dict:
    out = {}
    for k, campo in CAMPOS_ONS.items():
        if isinstance(campo, tuple):
            v = None
            for c in campo:
                if c in row:
                    v = _num(row.get(c))
                    break
            out[k] = v
        else:
            out[k] = _num(row.get(campo))
    # No arquivo horário do ONS a defluente de alguns reservatórios vem 0 com turbinada > 0 (ex.: Santa Branca,
    # out/2026). Nesses casos a defluente é reconstituída como turbinada + vertida + outras estruturas.
    partes = [out.get(k) for k in ("turb", "vert", "outras")]
    soma = sum(p for p in partes if p is not None)
    if (out.get("defl") in (None, 0.0)) and soma > 0:
        out["defl"] = soma
        out["defl_reconstituida"] = True
    return out


def ons_diario(anos: list[int], ids: set[str]) -> dict[str, dict[str, dict]]:
    """Retorna {id_reservatorio: {'aaaa-mm-dd': {nivel, vu, afl, defl, transf}}}."""
    hoje = dt.date.today()
    serie: dict[str, dict[str, dict]] = {i: {} for i in ids}
    for ano in anos:
        bruto = _cache(f"ons_di_{ano}.csv", ONS_DIARIO.format(ano=ano), renovar=(ano >= hoje.year - (1 if hoje.month == 1 else 0)))
        leitor = csv.DictReader(io.StringIO(bruto.decode("utf-8", errors="replace")), delimiter=";")
        for row in leitor:
            i = (row.get("id_reservatorio") or "").strip()
            if i in ids:
                serie[i][row["din_instante"][:10]] = _linha_ons(row)
    return serie


def ons_horario(meses: list[tuple[int, int]], ids: set[str]) -> dict[str, dict[str, dict]]:
    """Retorna {id: {'aaaa-mm-dd HH:MM': {...}}}. Meses = [(ano, mes), ...].
    O arquivo mensal do ONS tem ~22 MB; o cache guarda só as linhas dos reservatórios pedidos
    (ons_ho_<ano>_<mm>_<hash>.csv), o que permite manter anos de histórico horário."""
    import hashlib
    hoje = dt.date.today()
    chave = hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:8]
    serie: dict[str, dict[str, dict]] = {i: {} for i in ids}
    for ano, mes in meses:
        corrente = (ano, mes) >= ((hoje - dt.timedelta(days=5)).year, (hoje - dt.timedelta(days=5)).month)
        arq = CACHE / f"ons_ho_{ano}_{mes:02d}_{chave}.csv"
        if arq.exists() and not corrente:
            texto = arq.read_text(encoding="utf-8")
        else:
            try:
                bruto = baixar(ONS_HORARIO.format(ano=ano, mes=mes)).decode("utf-8", errors="replace")
            except RuntimeError as e:
                print(f"[aviso] horário {ano}-{mes:02d} indisponível: {e}")
                continue
            linhas = bruto.splitlines()
            cab = linhas[0] if linhas else ""
            col = cab.split(";").index("id_reservatorio") if "id_reservatorio" in cab else None
            manter = [cab] + [l for l in linhas[1:] if col is not None and len(l.split(";")) > col and l.split(";")[col].strip() in ids]
            texto = "\n".join(manter) + "\n"
            CACHE.mkdir(parents=True, exist_ok=True)
            arq.write_text(texto, encoding="utf-8")
        for row in csv.DictReader(io.StringIO(texto), delimiter=";"):
            i = (row.get("id_reservatorio") or "").strip()
            if i in ids:
                serie[i][row["din_instante"][:16]] = _linha_ons(row)
    return serie


# ----------------------------------------------------------------------------- SAR Cantareira
def _tabela_html(txt: str) -> list[list[str]]:
    linhas = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, flags=re.S | re.I):
        cels = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)
        cels = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cels]
        if cels:
            linhas.append(cels)
    return linhas


def sar_cantareira(codigo: int, inicio: dt.date, fim: dt.date) -> dict[str, dict]:
    """Série diária do SAR Cantareira: {'aaaa-mm-dd': {cota, vu_hm3, vu, afl, defl}}."""
    q = urllib.parse.urlencode({
        "dropDownListReservatorios": codigo,
        "dataInicial": inicio.strftime("%d/%m/%Y"),
        "dataFinal": fim.strftime("%d/%m/%Y"),
        "button": "Buscar",
    })
    txt = baixar(f"{SAR_CANTAREIRA}?{q}").decode("utf-8", errors="replace")
    linhas = _tabela_html(txt)
    cab = next((l for l in linhas if any(c.startswith("Data") for c in l)), None)
    if cab is None:
        return {}
    idx = {}
    for j, nome in enumerate(cab):
        n = nome.lower()
        if n.startswith("data"):
            idx["data"] = j
        elif n.startswith("cota"):
            idx["cota"] = j
        elif "volume" in n and "%" in n:
            idx["vu"] = j
        elif "volume" in n:
            idx["vu_hm3"] = j
        elif n.startswith("aflu"):
            idx["afl"] = j
        elif n.startswith("deflu"):
            idx["defl"] = j
    out: dict[str, dict] = {}
    for cel in linhas:
        if len(cel) != len(cab) or not re.match(r"\d{2}/\d{2}/\d{4}", cel[idx["data"]]):
            continue
        d = dt.datetime.strptime(cel[idx["data"]][:10], "%d/%m/%Y").date().isoformat()
        out[d] = {k: _num(cel[j]) for k, j in idx.items() if k != "data"}
    return out


# ----------------------------------------------------------------------------- Telemetria ANA
def telemetria(codigo: str, inicio: dt.date, fim: dt.date) -> dict[str, dict]:
    """Dados de 15 min/1 h: {'aaaa-mm-dd HH:MM': {nivel_cm, vazao, chuva}}."""
    q = urllib.parse.urlencode({
        "codEstacao": codigo,
        "dataInicio": inicio.strftime("%d/%m/%Y"),
        "dataFim": fim.strftime("%d/%m/%Y"),
    })
    raiz = ET.fromstring(baixar(f"{TELEMETRIA}?{q}"))
    out = {}
    for el in raiz.iter():
        if el.tag.endswith("DadosHidrometereologicos"):
            reg = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
            dh = reg.get("DataHora", "")[:16]
            if dh:
                out[dh] = {"nivel_cm": _num(reg.get("Nivel")), "vazao": _num(reg.get("Vazao")), "chuva": _num(reg.get("Chuva"))}
    return out


# ----------------------------------------------------------------------------- entrada manual
def manual(nome: str) -> dict[str, dict]:
    """Lê data/manual/<nome>.csv (';', ponto decimal, coluna 'data' aaaa-mm-dd).
    Usado para séries sem fonte primária aberta (ex.: Santa Inês, postos PCJ)."""
    arq = RAIZ / "data" / "manual" / f"{nome}.csv"
    if not arq.exists():
        return {}
    out = {}
    with arq.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            d = row.pop("data")
            out[d] = {k: _num(v) for k, v in row.items()}
    return out


# ----------------------------------------------------------------------------- SAISP (postos PCJ)
SAISP = "https://www.saisp.br/geral/processo.jsp"
SAISP_REFERER = "https://www.saisp.br/online/produtos-publicos/"  # obrigatório
SAISP_POSTOS = {"valinhos": 56, "atibaia": 54, "buenopolis": 138}  # WHICHCHANNEL, produto 56
SAISP_VAZAO = 3  # instrumento 3 = Q (m³/s); 1 = FLU (m); 100 = cota (m); 0 = chuva (mm)
_TZ_BR = dt.timezone(dt.timedelta(hours=-3))


def saisp(posto: int, inicio: dt.datetime, fim: dt.datetime, instrumento: int = SAISP_VAZAO) -> dict[str, float]:
    """Série de 10 min do SAISP: {'aaaa-mm-dd HH:MM' (hora local): valor}."""
    q = urllib.parse.urlencode({
        "PRODUTO": 56, "WHICHCHANNEL": posto, "WHICHCODE": 200,
        "SOMENTE_DADOS_INSTRUMENTOS": instrumento, "XSL_EXTRA": "g1",
        "DI": inicio.strftime("%d/%m/%Y %H:%M"), "DF": fim.strftime("%d/%m/%Y %H:%M"), "USERID": "Publico",
    })
    req = urllib.request.Request(f"{SAISP}?{q}", headers={"User-Agent": USER_AGENT, "Referer": SAISP_REFERER})
    ultimo = None
    for i in range(TENTATIVAS):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_contexto_ssl()) as r:
                txt = r.read().decode("utf-8", errors="replace")
            break
        except Exception as e:
            ultimo = e
            time.sleep(2 ** (i + 1))
    else:
        raise RuntimeError(f"SAISP posto {posto}: {ultimo}")
    m = re.search(rf"instrumData_{posto}_{instrumento}\s*=\s*\{{.*?values\s*:\s*(\[.*?\]\s*,?\s*\])", txt, re.S)
    if not m:
        return {}
    out = {}
    for t, v in re.findall(r"\[(\d+),(-?[\d.]+)\]", m.group(1)):
        out[dt.datetime.fromtimestamp(int(t) / 1000, _TZ_BR).strftime("%Y-%m-%d %H:%M")] = float(v)
    return out


def saisp_diario(posto: int, inicio: dt.date, fim: dt.date) -> dict[str, float]:
    """Média diária (hora local) da vazão SAISP. O servidor devolve no máximo ~5 dias por consulta: blocos de 4 dias.
    Meses encerrados há mais de 10 dias ficam em cache (saisp_<posto>_<aaaa-mm>.json), pois a coleta de anos é lenta."""
    import json
    limite_cache = dt.date.today() - dt.timedelta(days=10)
    out: dict[str, float] = {}
    m = inicio.replace(day=1)
    while m <= fim:
        prox = (m + dt.timedelta(days=32)).replace(day=1)
        a, b = max(m, inicio), min(prox - dt.timedelta(days=1), fim)
        fechado = (prox - dt.timedelta(days=1)) < limite_cache and b == prox - dt.timedelta(days=1)
        arq = CACHE / f"saisp_{posto}_{m:%Y-%m}.json"
        if fechado and arq.exists() and a == m:
            out.update(json.loads(arq.read_text(encoding="utf-8")))
        else:
            acum: dict[str, list[float]] = {}
            d = a
            while d <= b:
                d2 = min(d + dt.timedelta(days=3), b)
                serie = saisp(posto, dt.datetime.combine(d, dt.time(0, 0)), dt.datetime.combine(d2, dt.time(23, 50)))
                for k, v in serie.items():
                    acum.setdefault(k[:10], []).append(v)
                d = d2 + dt.timedelta(days=1)
            mes = {k: sum(v) / len(v) for k, v in acum.items() if len(v) >= 72}  # exige ≥ 50% dos registros do dia
            if fechado and a == m and mes:
                CACHE.mkdir(parents=True, exist_ok=True)
                arq.write_text(json.dumps(mes), encoding="utf-8")
            out.update(mes)
        m = prox
    return {k: v for k, v in out.items() if inicio.isoformat() <= k <= fim.isoformat()}


# ----------------------------------------------------------------------------- Sabesp, Portal dos Mananciais
# Chave: (idElemento, idParametro). Ids de /api/v4/elementos e /api/v4/series-temporais (verificados em 04/11/2026).
SABESP_SERIES = {
    "santa_ines": (36, 3),   # EEAB Santa Inês, vazão bombeada (m³/s)
    "jaguari_ps": (48, 3),   # EEAB Jaguari (PS), vazão transferida ao Atibainha (m³/s)
}
# Reservatórios do Cantareira: código SAR -> idElemento Sabesp; campo SAR -> idParametro
SABESP_CANTAREIRA = {"29001": 1, "29002": 3, "29003": 4, "29004": 5}
SABESP_CAMPOS_SAR = {"cota": 2, "vu": 684, "vu_hm3": 685, "defl": 689}
SABESP_BLOCO_DIAS = 366


def sabesp_serie(id_elemento: int, id_parametro: int, inicio: dt.date, fim: dt.date) -> dict[str, float]:
    """Série diária da API Mananciais da Sabesp: {'aaaa-mm-dd': valor}. O dia D vai de D-1 07:00 a D 07:00."""
    out: dict[str, float] = {}
    d = inicio
    while d <= fim:
        d2 = min(fim, d + dt.timedelta(days=SABESP_BLOCO_DIAS - 1))
        url = f"{SABESP_API}/elementos/{id_elemento}/dados/{id_parametro}/diario/{d.isoformat()}/{d2.isoformat()}"
        js = json.loads(baixar(url).decode("utf-8"))
        for reg in js.get("data") or []:
            if reg.get("value") is not None:
                out[reg["date"][:10]] = float(reg["value"])
        d = d2 + dt.timedelta(days=1)
    return out


def sabesp(chave: str, inicio: dt.date, fim: dt.date) -> dict[str, dict]:
    """Série nomeada (SABESP_SERIES) no formato {'aaaa-mm-dd': {'q': valor}}."""
    el, par = SABESP_SERIES[chave]
    return {d: {"q": v} for d, v in sabesp_serie(el, par, inicio, fim).items()}


def sabesp_cantareira(codigo: str, inicio: dt.date, fim: dt.date) -> dict[str, dict]:
    """Reservatório do Cantareira no formato de sar_cantareira(): {'aaaa-mm-dd': {cota, vu, vu_hm3, defl}}.
    Conferido com o SAR em out/2026: diferença máxima de 0,005 (arredondamento)."""
    el = SABESP_CANTAREIRA[str(codigo)]
    out: dict[str, dict] = {}
    for campo, par in SABESP_CAMPOS_SAR.items():
        for d, v in sabesp_serie(el, par, inicio, fim).items():
            out.setdefault(d, {})[campo] = v
    return out
