import sys; import os as _o; sys.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import contador_almas as ca, tempfile, os
def novo(conhecidos=()):
    p = ca.Progresso(os.path.join(tempfile.mkdtemp(),"p.json"))
    for n in conhecidos: p.somar(n,1,salvar=False)
    return p, ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar)
A="Obteve Pontos da Alma "
K="Voce obteve {} Kinah."
sob=lambda f,k: [(t,y-20*k,h) for t,y,h in f]
base=[(K.format(100+i),100+i*20,14) for i in range(5)]
# 1a alma de monstro novo, "x1." fora da area: linha parada por varios quadros
p,r=novo(["Supervisor Kuru"]); tot=[]
f=sob(base[1:],0)+[(A+"Lupyllini Modificado",200,14)]
for q in [base]+[f]*8: tot+=r.quadro_pos(q)
f2=sob(base[3:],2)+[(A+"Lupyllini Modificado",160,14),(K.format(360),180,14),(A+"Lupyllini Modificado",200,14)]
for q in [f2]*8: tot+=r.quadro_pos(q)
print("lupyllini:", tot, [ (m['nome'],m['qtd']) for m in p.monstros])
# Kalgolem: nome quebrado "de" nunca vira monstro
p,r=novo([]); tot=[]
f=base[1:]+[(A+"Kalgolem de",200,14)]
for q in [base]+[f]*8: tot+=r.quadro_pos(q)
print("kalgolem de (cortado):", tot)
