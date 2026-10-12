"""Matanca em massa de Espiritos com o nome quebrado em 2 linhas
('Obteve Pontos da Alma Espirito da Agua' / 'Intermediario x1.'), com
leituras falhando aqui e ali. Confere que nao conta a mais."""
import os, random, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca

NOMES = ["Espírito da Água Intermediário", "Espírito da Água Superior",
         "Espírito da Terra Intermediário", "Espírito do Vento Superior",
         "Espírito do Fogo Intermediário"]
A = "Obteve Pontos da Alma "


def linhas_da_alma(nome):
    p = nome.split()
    return [A + " ".join(p[:-1]), p[-1] + " x1."]


def rodar(seed, falha=0.15, rajada=(1, 6), conhecidos=True):
    rnd = random.Random(seed)
    p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
    if conhecidos:
        for n in NOMES:
            p.monstros.append({"nome": n, "qtd": 0, "apelidos": [], "atualizado": 0})
    r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar, p.comeco_de_outro)
    chat, real = [], {}
    kin = 100
    for _ in range(14):  # historico inicial
        kin += 7
        chat.append(f"Você obteve {kin} Kinah.")
    def quadro():
        vis = chat[-11:]
        out = []
        for i, t in enumerate(vis):
            if rnd.random() < falha:
                continue  # OCR perdeu a linha neste quadro
            ruido = rnd.choice(["", "", "", " e", " .", " o"]) if "Alma" in t else ""
            out.append((t + ruido, 100 + i * 20, 14))
        return out
    for f in [quadro() for _ in range(3)]:
        r.quadro_pos(f)
    for _ in range(25):
        for _ in range(rnd.randint(*rajada)):
            n = rnd.choice(NOMES)
            real[n] = real.get(n, 0) + 1
            chat.extend(linhas_da_alma(n))
            if rnd.random() < 0.6:
                kin += 13
                chat.append(f"Você obteve {kin} Kinah.")
        for _ in range(rnd.randint(2, 5)):
            for n in r.quadro_pos(quadro()):
                p.somar(n, 1, salvar=False)
    for _ in range(6):
        for n in r.quadro_pos(quadro()):
            p.somar(n, 1, salvar=False)
    cont = {m["nome"]: m["qtd"] for m in p.monstros if m["qtd"]}
    return real, cont


mais = menos = errado = 0
for seed in range(40):
    real, cont = rodar(seed)
    for n in set(real) | set(cont):
        d = cont.get(n, 0) - real.get(n, 0)
        if n not in NOMES:
            errado += cont.get(n, 0)
        elif d > 0:
            mais += d
        else:
            menos -= d
tot = sum(sum(rodar(s)[0].values()) for s in range(40))
print(f"almas reais: {tot}  a mais: {mais}  a menos: {menos}  em monstro errado: {errado}")
print("OK" if mais == 0 and errado == 0 else "XX")
