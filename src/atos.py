"""atos.py - Cadastro único dos atos complementares às resoluções (config/atos_complementares.json).

Um ato complementar é um documento fora da resolução que fixa ou altera o limite ou o gatilho de uma regra:
outorga, comunicado, ofício de manifestação sobre curvas, valor mensal fixado ou situação declarada pelo operador.
Este módulo lê o cadastro, confere a consistência dos registros e entrega os valores no formato usado por
src/regras.py e src/calculo.py.

Uso: python src/atos.py   (confere o cadastro e lista os atos por regra)
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent.parent / "config" / "atos_complementares.json"

TIPOS = {"outorga", "comunicado", "oficio", "confirmacao", "registro", "valor_mensal", "situacao_declarada"}
EFEITOS = {"parametro", "revisao_estagio", "tolerancia", "curva_seguranca", "valores_mensais", "situacao"}
DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CadastroInvalido(ValueError):
    pass


def _conferir(atos: list[dict]) -> None:
    erros, ids = [], set()
    for i, a in enumerate(atos):
        rot = a.get("id") or f"ato nº {i + 1}"
        for campo in ("id", "tipo", "documento", "regras", "vigencia", "citacao", "efeito"):
            if campo not in a:
                erros.append(f"{rot}: falta o campo '{campo}'")
        if a.get("id") in ids:
            erros.append(f"{rot}: id repetido")
        ids.add(a.get("id"))
        if a.get("tipo") not in TIPOS:
            erros.append(f"{rot}: tipo '{a.get('tipo')}' fora de {sorted(TIPOS)}")
        ef = a.get("efeito") or {}
        if not EFEITOS & set(ef):
            erros.append(f"{rot}: efeito sem nenhum de {sorted(EFEITOS)}")
        if "parametro" in ef and "valor" not in ef:
            erros.append(f"{rot}: efeito 'parametro' sem 'valor'")
        vig = a.get("vigencia") or {}
        for k in ("inicio", "fim"):
            if vig.get(k) is not None and not DATA.match(vig[k]):
                erros.append(f"{rot}: vigencia.{k} '{vig[k]}' fora do formato aaaa-mm-dd")
        if vig.get("inicio") and vig.get("fim") and vig["fim"] < vig["inicio"]:
            erros.append(f"{rot}: vigência termina antes de começar")
        if a.get("data") is not None and not DATA.match(str(a["data"])):
            erros.append(f"{rot}: data '{a['data']}' fora do formato aaaa-mm-dd")
        if "revisao_estagio" in ef and not a["vigencia"].get("fim"):
            erros.append(f"{rot}: revisão de estágio precisa de vigência com fim")
    if erros:
        raise CadastroInvalido("config/atos_complementares.json:\n  " + "\n  ".join(erros))


@lru_cache(maxsize=None)
def _carregar(caminho: str) -> tuple:
    p = Path(caminho)
    if not p.exists():
        return ()
    atos = json.loads(p.read_text(encoding="utf-8")).get("atos", [])
    _conferir(atos)
    return tuple(atos)


def todos(caminho: Path = ARQUIVO) -> list[dict]:
    return [dict(a) for a in _carregar(str(caminho))]


def _com(efeito: str) -> list[dict]:
    return [a for a in todos() if efeito in a["efeito"]]


def parametros(base: dict) -> dict:
    """PARAMETROS de src/regras.py com os valores dos atos: limite fixo (valor/fonte) ou limite por ano (por_ano/fonte_por_ano)."""
    out = {k: dict(v) for k, v in base.items()}
    for a in _com("parametro"):
        ef = a["efeito"]
        p = out.setdefault(ef["parametro"], {})
        if "ano" in ef:
            p.setdefault("por_ano", {})[ef["ano"]] = ef["valor"]
            p.setdefault("fonte_por_ano", {})[ef["ano"]] = a["citacao"]
        else:
            p["valor"], p["fonte"] = ef["valor"], a["citacao"]
    for p in out.values():  # ordem estável por ano
        for k in ("por_ano", "fonte_por_ano"):
            if k in p:
                p[k] = dict(sorted(p[k].items()))
    return out


def revisoes_estagio(regra_id: str) -> list[dict]:
    """Revisões temporárias dos mínimos dos estágios (1.382/2015, art. 1º, § 2º), no formato da regra 1382-estagios."""
    out = []
    for a in _com("revisao_estagio"):
        if regra_id in a["regras"]:
            r = a["efeito"]["revisao_estagio"]
            out.append(dict(fonte=r["reservatorio"], estagio=r["estagio"], valor=r["valor"],
                            inicio=a["vigencia"]["inicio"], fim=a["vigencia"]["fim"], ato=a["citacao"]))
    return sorted(out, key=lambda r: r["inicio"])


def tolerancia(regra_id: str) -> dict | None:
    """Tolerância admitida por ato para a regra: {'tol', 'tol_desde', 'tol_ato'}."""
    for a in _com("tolerancia"):
        if regra_id in a["regras"]:
            return {"tol": a["efeito"]["tolerancia"], "tol_desde": a["vigencia"]["inicio"], "tol_ato": a["citacao"]}
    return None


def manifestacoes_curvas() -> dict[str, str]:
    """{ciclo: texto da manifestação da ANA} para as curvas de segurança do São Francisco (2.081/2017, art. 4º)."""
    return {a["efeito"]["curva_seguranca"]: a["citacao"] for a in _com("curva_seguranca")}


def valores_mensais() -> dict[str, float]:
    """{'<parâmetro>|aaaa-mm': valor} dos valores mensais fixados por ato (tetos da 2.081/2017, nível operacional da 614/2010)."""
    out = {}
    for a in _com("valores_mensais"):
        ef = a["efeito"]["valores_mensais"]
        for mes, v in ef["valores"].items():
            out[f"{ef['parametro']}|{mes}"] = v
    return out


def citacao_mensal(chave: str) -> str | None:
    """Texto do ato que fixou o valor mensal '<parâmetro>|aaaa-mm'."""
    par, mes = chave.split("|")
    for a in _com("valores_mensais"):
        ef = a["efeito"]["valores_mensais"]
        if ef["parametro"] == par and mes in ef["valores"]:
            return a["citacao"]
    return None


def situacoes() -> list[dict]:
    """Situações declaradas (só observação; não mudam a avaliação), no formato usado por calculo.obs_eventos."""
    return [{"id": a["id"], "sistema": a["efeito"]["situacao"], "inicio": a["vigencia"]["inicio"], "fim": a["vigencia"].get("fim"),
             "regras": a["regras"], "obs": a["citacao"], "fonte": a["documento"]} for a in _com("situacao")]


def _num(v: float, casas: int | None = None) -> str:
    """Número no padrão brasileiro (vírgula decimal, ponto de milhar)."""
    t = f"{v:,.{2 if casas is None else casas}f}"
    if casas is None and "." in t:
        t = t.rstrip("0").rstrip(".")
    return t.replace(",", "§").replace(".", ",").replace("§", ".")


def efeito_txt(a: dict) -> str:
    """Efeito do ato em uma frase curta, para a lista de atos do painel."""
    ef = a["efeito"]
    if "parametro" in ef:
        p = ef["parametro"]
        if p.startswith("outorga_"):
            return f"vazão outorgada de {_num(ef['valor'])} m³/s"
        if p == "vol_anual_jaguari":
            return f"volume anual de {ef.get('ano', '')}: {_num(ef['valor'])} hm³".replace(" :", ":")
        return f"{p} = {_num(ef['valor'])}" + (f" ({ef['ano']})" if "ano" in ef else "")
    if "revisao_estagio" in ef:
        r = ef["revisao_estagio"]
        return f"mínimo do estágio {r['estagio']} em {r['reservatorio']}: {_num(r['valor'])}% VU"
    if "tolerancia" in ef:
        return f"tolerância de {_num(ef['tolerancia'] * 100)}%"
    if "curva_seguranca" in ef:
        return f"curvas de segurança do ciclo {ef['curva_seguranca']}"
    if "valores_mensais" in ef:
        v = ef["valores_mensais"]
        meses = sorted(v["valores"])
        return f"{v['parametro']}: {len(meses)} valor(es) mensal(is), {meses[0]} a {meses[-1]}" if meses else v["parametro"]
    if "situacao" in ef:
        return f"situação declarada ({ef['situacao']}); só observação"
    return ", ".join(sorted(ef))


def resumo() -> list[dict]:
    """Lista enxuta dos atos para o painel (aba de atos complementares)."""
    campos = ("id", "tipo", "documento", "data", "sei", "regras", "vigencia", "citacao")
    return [{**{k: a.get(k) for k in campos}, "efeito_txt": efeito_txt(a)} for a in todos()]


def cobre(a: dict, inicio: str, fim: str) -> bool:
    """O ato está vigente em algum dia de [inicio, fim] (datas aaaa-mm-dd; vigência sem início ou sem fim é aberta)."""
    v = a.get("vigencia") or {}
    return (v.get("inicio") or "0000-01-01") <= fim and (v.get("fim") or "9999-12-31") >= inicio


# Regras em que, sem o ato, o painel usa uma aproximação (o mês é avaliado, mas não com o valor do ato).
APROXIMACAO = {"49-RIUHFC", "70-TOSMES-praias"}


def cobertura(regra_id: str, serie: list[dict], pendente: str = "Parâmetro pendente", nao_aplica: str = "Não se aplica") -> dict[str, str]:
    """Cobertura mensal da regra pelo cadastro de atos, a partir da série diária já avaliada.

    'lacuna': algum dia do mês ficou em Parâmetro pendente (falta o ato);
    'ato':    há ato do cadastro vigente no mês para a regra;
    'aprox':  sem ato, o painel avaliou o mês com aproximação (APROXIMACAO);
    'sem':    o mês não exige ato (regra não se aplica ou o limite vem da própria resolução).
    """
    meses: dict[str, list[str]] = {}
    for s in serie:
        meses.setdefault(s["d"][:7], []).append(s["st"])
    atos_r = [a for a in todos() if regra_id in a["regras"] and not ({"situacao"} & set(a["efeito"]))]
    out = {}
    for m, sts in meses.items():
        a0, a1 = m + "-01", m + "-31"
        if pendente in sts:
            out[m] = "lacuna"
        elif all(st == nao_aplica for st in sts):
            out[m] = "sem"
        elif any(cobre(a, a0, a1) for a in atos_r):
            out[m] = "ato"
        elif regra_id in APROXIMACAO:
            out[m] = "aprox"
        else:
            out[m] = "sem"
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import regras
    ids = {r["id"] for r in regras.REGRAS}
    atos = todos()
    sem_regra = [(a["id"], r) for a in atos for r in a["regras"] if r not in ids]
    print(f"{len(atos)} atos em {ARQUIVO.name}")
    for a in atos:
        v = a["vigencia"]
        print(f"  {a['id']:<28} {a['tipo']:<18} {v.get('inicio') or '—'} a {v.get('fim') or '—'}  {', '.join(a['regras'])}")
    if sem_regra:
        print("Regras citadas que não existem em src/regras.py:", sem_regra)
        sys.exit(1)
