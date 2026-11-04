#!/usr/bin/env python3
"""Coletor do Olho na Copa.

Busca atos de manejo arbóreo no Diário Oficial da Cidade de São Paulo (DOC),
filtra, classifica e acrescenta ao registros.txt.

Uso: python3 -I coletor.py registros.txt [AAAA-MM-DD_inicio AAAA-MM-DD_fim] [--dry]
Sem datas: coleta do dia seguinte ao "fim" do cabeçalho até hoje.
Imprime um resumo em JSON na última linha.
"""
import sys, re, json, ssl, os, html, datetime as dt
import urllib.request, urllib.parse, http.cookiejar
from concurrent.futures import ThreadPoolExecutor

BASE = "https://diariooficial.prefeitura.sp.gov.br/"
URL_BUSCA = BASE + "md_epubli_controlador.php?acao=materias_pesquisar"
TERMOS = ["árvore", "arbóreo", "arbórea", "vegetação", "transplante", "espécime"]

ca = "/root/.ccr/ca-bundle.crt"
CTX = ssl.create_default_context(cafile=ca) if os.path.exists(ca) else ssl.create_default_context()
CJ = http.cookiejar.CookieJar()
OP = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CJ), urllib.request.HTTPSHandler(context=CTX))


def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0 OlhoNaCopa"})
    for tent in range(3):
        try:
            return OP.open(req, timeout=60).read().decode("latin-1")
        except Exception:
            if tent == 2:
                raise


def texto(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def busca(termo, ini, d0, d1):
    d = {"hdnTermoPesquisa": termo, "hdnTipoPesquisa": "Q", "hdnVersaoDiario": "A", "hdnOndePesquisa": "",
         "hdnTipoDataPesquisa": "P", "hdnDataInicioPesquisa": d0, "hdnDataFimPesquisa": d1,
         "hdnTipoDocumentoPesquisa": "", "hdnVeiculoPublicacao": "", "hdnDataPublicacao": "", "hdnOrgaoFiltro": "",
         "hdnUnidadeResponsavelFiltro": "", "hdnTipoDocumentoFiltro": "", "hdnInicio": str(ini),
         "hdnVisualizacao": "S", "hdnModoPesquisa": "AVANCADA"}
    return get(URL_BUSCA, urllib.parse.urlencode(d, encoding="latin-1").encode())


def resultados(h):
    """Lê a visão "sumário": órgão > unidade > documentos. Devolve o cabeçalho no formato da lista."""
    out, org, uni = [], "", ""
    pat = re.compile(r'<span class="detalhesOrgao">(.*?)</span></span>|<span class="detalhesUnidade">(.*?)</span></span>|<div class="dadosDocumento">(.*?)(?=<div class="dadosDocumento">|<li><i class="detalhesAgrupamento">|</ul></li></ul>|$)', re.S)
    for m in pat.finditer(h):
        if m.group(1) is not None:
            org = texto(m.group(1))
        elif m.group(2) is not None:
            uni = texto(m.group(2))
        else:
            bloco = m.group(3)
            lk = re.search(r'md_epubli_visualizar\.php\?([^"\'\s>]+)', bloco)
            t = texto(bloco)
            dn = re.search(r"Documento: ?(\d+)", t)
            if lk and dn:
                out.append({"t": f"{org} > {uni} {t}", "link": html.unescape(lk.group(1)), "doc": dn.group(1)})
    return out


# ---------- classificação ----------
ORG_OK = re.compile(r"^(SUB-|SVMA|SMSUB)")
ASS_RUIM = re.compile(r"Indeniza|Espelho Pubnet|verba locat|Orçamento|Pagamentos|(?:^|[\s:])Licitação|Eventos|Permissão|Sistema viário|Atestado|Restauro|SISACOE|Fiscalização de Posturas|Procedimento Interno|Concorrência|Pregão", re.I)
EXC = re.compile(r"supress[ãa]o (de itens|de valor|contratual|de metas|de despesas|do objeto|de cl[aá]usula|de inst[âa]ncia|de direitos|de par[aá]grafo|de artigo)|aditivo de supress|poda de (grama|mato|canas)|manuten[çc][ãa]o predial|servi[çc]os de copa", re.I)
INC_A = re.compile(r"poda|supress|transplante|corte|manejo|remo[çc]|compensa", re.I)
INC_B = re.compile(r"[áa]rvore|arb[óo]re|vegeta[çc]|esp[ée]cime|exemplar", re.I)
BOM = re.compile(r"Poda e Remo|remoção|manejo arb|poda em manejo|TCA|Compromisso Ambiental|Parecer Técnico Ambiental", re.I)
LBL = r"(?:Int|Interessad[oa]s?|INTERESSAD[OA]S?|Requerente|REQUERENTE|Solicitante|SOLICITANTE|Infrator|Síndic[oa]|Representante|Propriet[áa]ri[oa])"
RED = re.compile("(" + LBL + r")\s*:\s*(?:.{1,90}?(?=\s(?:[A-ZÁÉÍÓÚ][A-Za-zçãáéíóú/]{2,20}:|DESPACHO|Endereço|ENDEREÇO|CNPJ|CPF|Processo|SEI|Assunto|Ass|ASSUNTO|I\s?-|1\.))|(?:\S+\s?){1,5})")
END = re.compile(r"\b(?:Rua|R\.|Avenida|Av\.|Alameda|Al\.|Pra[çc]a|Travessa|Estrada|Viela|Largo|Viaduto|Rodovia)\s+[^,;:()]{2,60}?(?:(?:,|\s)\s*(?:n[º°o]\.?\s*)?\d{1,5}\b|(?=\s*[,;.]|\s+-\s|\s+Bairro|\s+CEP))", re.I)
DECIS = re.compile(r"\b(INDEFIRO|DEFIRO|AUTORIZO|Autorizo|autoriza-se|COMUNICO|comunicamos|Comunicamos|INDEFERIDO|Onde se lê|convalid|EXTRATO|CERTIFICADO)")


def corpo(full):
    s = full
    i = re.search(r"Telefone:", s)
    if i and i.start() < 900:
        s = s[i.end():]
        s = re.sub(r"^\s*[\d\s()\-]*", "", s)
    j = re.search(r"A autenticidade deste documento|Referência: Processo", s)
    return s[:j.start()] if j else s


def classifica(t, full, link):
    m = re.match(r"^(\S+) .+? > (\S+) (.*?) ?Processo: (\S+) - (.*?)Documento: (\d+) - (.*?)Publicado em (\d\d)/(\d\d)/(\d{4})", t)
    if not m:
        return None, "sem estrutura"
    org, unid, _, proc, assunto, doc, tipo, dd, mm, aa = [x.strip() for x in m.groups()]
    unid = unid.replace(org + "/", "")
    c = corpo(full)
    if not ORG_OK.match(org):
        return None, "órgão fora do tema"
    if ASS_RUIM.search(assunto):
        return None, "assunto fora do tema"
    if re.search(r"Ata de Reuni|Notifica", tipo) or "SFISC" in unid or re.search(r"Auto de Infra[çc][ãa]o|Auto de Multa|Infrator", c[:1200]):
        return None, "ata ou fiscalização"
    bom = bool(BOM.search(assunto))
    if not bom and EXC.search(c):
        return None, "termo de exclusão"
    if not bom and not (INC_A.search(c) and INC_B.search(c)) and "Retifica" not in tipo:
        return None, "sem termos de manejo"
    ls = re.search(r"Leia-se:?(.{0,80})", c, re.I)
    if "Retifica" in tipo and ls and not re.search(r"Rua|Avenida|Av\.|Alameda|Pra[çc]a|Travessa|Estrada|n[º°o]\s*\d|exemplar|árvore", ls.group(1)[:45], re.I):
        return None, "retificação só de nome"
    low = c.lower()
    frases = " ".join(s for s in re.split(r"(?<=\.)\s", c) if not re.search(r"10\.919|10\.365|Lei |Decreto", s) or re.search(r"DEFIRO|AUTORIZO|autoriza-se|COMUNICO|comunicamos", s, re.I)).lower().replace("supressão/poda", "")
    tca = bool(re.search(r"TCA|Compromisso Ambiental|Parecer Técnico Ambiental", assunto) or unid.endswith("TCA"))
    ac = ""
    if "Retifica" in tipo: ac += "X"
    if tca: ac += "C"
    if re.search(r"remo[çc][ãa]o|supress[ãa]o|corte", frases) or re.search(r"remo[çc]", assunto, re.I): ac += "R"
    if re.search(r"\bpoda", frases) or (re.search("poda", assunto, re.I) and "Poda e Remo" not in assunto): ac += "P"
    if "transplante" in frases: ac += "T"
    if not ac: ac = "R"
    head = tipo + " " + c[:300]
    if re.search("indeferi", head, re.I): dc = "I"
    elif "Retifica" in tipo: dc = "X"
    elif re.search(r"DEFIRO|AUTORIZO|autoriza-se|deferido|autorizad", tipo + " " + c, re.I): dc = "D"
    elif re.search("comunica", tipo + " " + assunto + " " + c[:400], re.I): dc = "C"
    else: dc = "D"
    urg = "1" if re.search(r"urg[êe]ncia|emergencial", c, re.I) else "0"
    if tca: ar = "O"
    elif "parque" in low and re.search(r"[áa]rea interna", low): ar = "Q"
    elif re.search(r"[áa]rea interna|im[óo]vel particular|condom[íi]nio|\blote\b", low + " " + assunto.lower()): ar = "I"
    else: ar = "E"
    locs = []
    pq = re.search(r"Parque [A-ZÁÉÍÓÚ][\wÀ-ú\-“”\" ]{2,50}?(?=,| situad| -|\.| Assunto)", c)
    if pq: locs.append(pq.group(0).strip())
    for x in END.finditer(c):
        s = re.split(r" DESPACHO| Assunto| Interessad", re.sub(r"\s+", " ", x.group(0)))[0].strip(" ,.-")
        if len(s) > 6 and not re.search(r"Paraiso, 387|Alvares Penteado, 49|Giovanni Gronchi|Armando de Arruda|Augusto Carlos Bauman|Diogo de Faria|Patriarca", s, re.I):
            if not any(o.lower().startswith(s.lower()) or s.lower().startswith(o.lower()) for o in locs):
                locs.append(s)
    locs = locs[:3]
    q = ""
    if not tca:
        nums = [int(n) for n in re.findall(r"\b0?(\d{1,3})\s*(?:\([^)]{1,25}\))?\s*(?:exemplar(?:es)?|[áa]rvores?|indiv[íi]duos? arb)", c, re.I) if 0 < int(n) < 500]
        q = str(sum(nums)) if nums else ""
    pr = re.search(r"(\d{1,2})\s*\(?[a-zç]*\)?\s*dias(?: [úu]teis)?[^.]{0,80}recurso", c, re.I)
    rc = pr.group(1).lstrip("0") if pr else ""
    cr = RED.sub(lambda mm: mm.group(1) + ": [omitido] ", c)
    cr = re.sub(r"\s+", " ", cr)
    i = DECIS.search(cr)
    x = cr[i.start():] if i else cr
    if x.startswith("Onde se lê"):
        x = re.sub(r"Onde se lê:.*?Leia-se:", "Retificação, leia-se:", x)
    if len(x) > 130:
        x = x[:130]
        x = x[:x.rfind(" ")] + "…"
    if "[omitido]" in x:
        x = {"P": "Comunicação ou autorização de poda", "R": "Autorização ou pedido de remoção"}.get(ac[-1:], "Ato de manejo arbóreo") + f" (SEI {proc}). Nome do interessado omitido."
    limpa = lambda s: re.sub(r"[¦\n\r]", " ", s).strip()
    campos = [doc, f"{aa}-{mm}-{dd}", org, unid, proc, limpa(tipo), ac, dc, urg, ar, limpa(" ; ".join(locs)), q, rc, limpa(x), link]
    return campos, None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    path = args[0]
    linhas = open(path, encoding="utf8").read().splitlines()
    cab = [l for l in linhas if l.startswith("#")]
    dados = [l for l in linhas if l and not l.startswith("#")]
    conhecidos = {l.split("¦")[0] for l in dados}
    meta = next((l for l in cab if l.startswith("# atualizado")), None)
    f = meta.split("¦") if meta else None
    hoje = dt.date.today()
    if len(args) >= 3:
        d0, d1 = dt.date.fromisoformat(args[1]), dt.date.fromisoformat(args[2])
    else:
        d0 = dt.date.fromisoformat(f[4]) + dt.timedelta(days=1)
        d1 = hoje
    resumo = {"de": str(d0), "ate": str(d1), "novos": 0, "descartados": {}}
    achados = {}
    if d0 <= d1:
        get(URL_BUSCA)
        s0, s1 = d0.strftime("%d/%m/%Y"), d1.strftime("%d/%m/%Y")
        dia = d0
        while dia <= d1:  # um dia por vez: a visão "sumário" traz todos os resultados numa página
            sd = dia.strftime("%d/%m/%Y")
            for termo in TERMOS:
                h = busca(termo, 0, sd, sd)
                rs = resultados(h)
                tot = re.search(r"Exibindo (\d+)", texto(h))
                if tot and int(tot.group(1)) > len({r["doc"] for r in rs}):
                    resumo.setdefault("avisos", []).append(f"{sd} '{termo}': {tot.group(1)} resultados, {len(rs)} lidos")
                for r in rs:
                    if r["doc"] not in conhecidos:
                        achados.setdefault(r["doc"], r)
            dia += dt.timedelta(days=1)
        def baixa(r):
            try:
                r["full"] = texto(get(BASE + "md_epubli_visualizar.php?" + r["link"]))
            except Exception:
                r["full"] = ""
            return r
        with ThreadPoolExecutor(6) as ex:
            lista = list(ex.map(baixa, achados.values()))
        novos = []
        if dry: resumo["vistos"] = {r["doc"]: None for r in lista}
        for r in lista:
            campos, motivo = classifica(r["t"], r["full"], r["link"])
            if motivo:
                if dry: resumo["vistos"][r["doc"]] = motivo
                resumo["descartados"][motivo] = resumo["descartados"].get(motivo, 0) + 1
            else:
                novos.append(campos)
        novos.sort(key=lambda c: (c[1], c[2]), reverse=True)
        resumo["novos"] = len(novos)
        resumo["remocoes_autorizadas"] = sum(1 for c in novos if "R" in c[6] and c[7] == "D")
        resumo["urgencias"] = sum(1 for c in novos if c[8] == "1")
        por = {}
        for c in novos:
            por[c[2]] = por.get(c[2], 0) + 1
        resumo["por_orgao"] = dict(sorted(por.items(), key=lambda kv: -kv[1]))
        resumo["destaques"] = [{"doc": c[0], "data": c[1], "orgao": c[2], "local": c[10], "acoes": c[6], "urgencia": c[8] == "1"}
                               for c in novos if "R" in c[6] and c[7] in "DC" and c[9] in "EQ"][:8]
        resumo["linhas"] = ["¦".join(c) for c in novos] if dry else None
        dados = ["¦".join(c) for c in novos] + dados
    ultima = max([l.split("¦")[1] for l in dados] or [str(d1)])
    fim = str(max(d1, dt.date.fromisoformat(ultima))) if d0 <= d1 else f[4]
    inicio = f[3] if f else str(d0)
    cab = [l for l in cab if not l.startswith("# atualizado")] + [f"# atualizado¦{hoje}¦periodo¦{inicio}¦{fim}"]
    if not dry:
        open(path, "w", encoding="utf8").write("\n".join(cab + dados) + "\n")
    print(json.dumps(resumo, ensure_ascii=False))


if __name__ == "__main__":
    main()
