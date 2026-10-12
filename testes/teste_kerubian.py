"""Kerubian e Kerubiel sao bichos diferentes (so mudam 2 letras no fim)."""
import os, random, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca
rnd = random.Random(3)
p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
p.monstros.append({"nome": "Kerubiel", "qtd": 82, "apelidos": [], "atualizado": 0})
r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar, p.comeco_de_outro)
A = "Obteve Pontos da Alma "
chat = [f"Você obteve {100 + i * 37} Kinah." for i in range(8)]
real = {"Kerubiel": 0, "Kerubian": 0}
lixo = ["", "", " .", " o", "i.", "xi."]
kin = 900
def quadro():
    out = []
    for i, t in enumerate(chat[-9:]):
        if "Alma" in t and t.endswith(" x1.") and rnd.random() < 0.2:
            t = t[:-4] + rnd.choice(["xi.", "xl;", " x1. o", " xt."])  # OCR
        out.append((t, 100 + i * 20, 14))
    return out
for _ in range(3):
    r.quadro_pos(quadro())
for _ in range(40):
    n = rnd.choice(["Kerubiel", "Kerubian"])
    real[n] += 1
    chat.append(A + n + " x1.")
    kin += rnd.randint(50, 500)
    chat.append(f"Você obteve {kin} Kinah.")
    for _ in range(3):
        for nome in r.quadro_pos(quadro()):
            p.somar(nome, 1, salvar=False)
res = {m["nome"]: m["qtd"] for m in p.monstros}
print("real:", real, "contado:", res)
ok = res.get("Kerubian") == real["Kerubian"] and res.get("Kerubiel") == 82 + real["Kerubiel"] and len(res) == 2
print("OK" if ok else "XX")
