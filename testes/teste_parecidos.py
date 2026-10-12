"""Monstros com o mesmo comeco e final diferente nao podem se misturar."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca
p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar, p.comeco_de_outro)
A = "Obteve Pontos da Alma "
for n in ["Espírito da Água Intermediário", "Espírito da Água Superior", "Espírito da Terra Intermediário",
          "Espirito da Agua Superior", "Espírito do Vento Superior", "Espiritô da Água Superor",
          "Espírito da Terra Intermediário", "Espírito da Água"]:
    p.somar(r.extrator.nome_em(A + n + " x1."), 1, salvar=False)
res = {m["nome"]: m["qtd"] for m in p.monstros}
print(res)
esperado = {"Espírito da Água Intermediário": 1, "Espírito da Água Superior": 3,
            "Espírito da Terra Intermediário": 2, "Espírito do Vento Superior": 1, "Espírito da Água": 1}
print("OK" if res == esperado else "XX")
# linha cortada ambigua nao vai para nenhum
print("completar ambiguo:", p.completar("Espírito da Água"), "| comeco de outro:", p.comeco_de_outro("Espírito da Água"))
