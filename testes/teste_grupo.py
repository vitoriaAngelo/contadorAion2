"""Almas que caem para outra pessoa do grupo nao contam; as suas sim."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca
ex = ca.Extrator(ca.PADROES_PADRAO)
do_grupo = ["Hagi obteve Alma: Kerubian (Vinculado)", "através da distribuição do grupo.",
            "Hagi obteve Alma: Kerubian (Vinculado) através da distribuição do grupo.",
            "Hagi obteve Alma Kerubian (Vinculado)", "Hagi obteve Alma da Kerubian (Vinculado)",
            "Hagi obteve Alma de Kerubiel (Vincilado)", "Hagi obteve Pontos da Alma: Kerubian (Vinculado)",
            "Ha9i obtevé Alma; Kerubiel (Vinculad0)", "Hagi obteve Alma: Espírito da Água Intermediário",
            "Hagi obteve Alma: Kerubar", "Fulano obteve Alma de Kerubiel (Vinculado) através"]
meus = {"Obteve Pontos da Alma Kerubian x1.": "Kerubian", "Obteve Pantos da Alma Kerubian xt. . e": "Kerubian",
        "Obteve Poritôs da Alma:Keribiansal,": None, "“Obteve Pontós da Alma Kerúbian x1. tus Re": "Kerúbian",
        "Obteve Pontos da Alma Espírito da Água Intermediário x1.": "Espírito da Água Intermediário",
        "Obteve Pontos da Alma Supervisor Kuru x1.": "Supervisor Kuru"}
ok = True
for l in do_grupo:
    n = ex.nome_em(l)
    print("grupo ", "OK" if n is None else "XX", repr(l), "->", n)
    ok &= n is None
for l, esp in meus.items():
    n = ex.nome_em(l)
    bom = n is not None if esp is None else (n == esp)
    print("meu   ", "OK" if bom else "XX", repr(l), "->", n)
    ok &= bom
print("OK" if ok else "XX")

# chat com almas suas e do grupo misturadas
import random, tempfile
rnd = random.Random(5)
p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar, p.comeco_de_outro)
chat = [f"Você obteve {100 + i * 31} Kinah." for i in range(9)]
kin, meu = 1000, 0
for _ in range(3):
    r.quadro_pos([(t, 100 + i * 20, 14) for i, t in enumerate(chat[-10:])])
for _ in range(40):
    if rnd.random() < 0.5:
        chat.append("Obteve Pontos da Alma Kerubian x1."); meu += 1
    else:
        chat.append("Hagi obteve Alma: " + rnd.choice(["Kerubian", "Kerubiel", "Kerubar"]) + " (Vinculado)")
        chat.append("através da distribuição do grupo.")
    kin += rnd.randint(20, 400); chat.append(f"Você obteve {kin} Kinah.")
    for _ in range(3):
        for n in r.quadro_pos([(t, 100 + i * 20, 14) for i, t in enumerate(chat[-10:])]):
            p.somar(n, 1, salvar=False)
res = {m["nome"]: m["qtd"] for m in p.monstros}
print("minhas almas:", meu, "| contado:", res)
print("OK" if res == {"Kerubian": meu} else "XX")
