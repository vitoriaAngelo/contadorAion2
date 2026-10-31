import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca


class Regressoes(unittest.TestCase):
    def test_dps_fica_em_ordem_do_maior_dano(self):
        nomes = ['Cleric', 'Gladiator', 'Ranger', 'Templar']
        danos = {'Cleric': 800, 'Gladiator': 2500, 'Ranger': 1200, 'Templar': 1200}
        self.assertEqual(
            ca.ordenar_dps(nomes, danos),
            ['Gladiator', 'Ranger', 'Templar', 'Cleric'],
        )

    def test_tempo_de_farm_mostra_horas_acima_de_99(self):
        self.assertEqual(ca.formatar_tempo_farm(3661), '01:01:01')
        self.assertEqual(ca.formatar_tempo_farm(360000), '100:00:00')

    def test_nomes_ambiguos_e_palavras_extras(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            p = ca.Progresso(os.path.join(pasta, 'p.json'))
            p.monstros = [dict(nome=n, apelidos=[]) for n in
                          ['Lobo de Gelo', 'Lobo de Galo', 'Zaif Mutante']]
            self.assertIsNone(p.achar('Lobo de Gulo'))
            self.assertIsNone(p.achar('Zeit Mutante'))
            self.assertIsNone(p.achar('Lobo Maior de Gelo'))
            self.assertEqual(p.achar('Lobo de Gelo')['nome'], 'Lobo de Gelo')
            self.assertEqual(p.achar('Zaif Mutanle')['nome'], 'Zaif Mutante')

    def test_rajada_substitui_chat_sem_recontar(self):
        r = ca.Rastreador(ca.PADROES_PADRAO)
        def quadro(linhas):
            return [(t, 100 + i * 38, 22) for i, t in enumerate(linhas)]
        antigo = quadro(['Voce obteve 123 Kinah.',
                         'Voce obteve 456 Kinah.',
                         'Obteve Pontos da Alma Guerreiro Mumu x1.',
                         'Obteve Pontos da Alma Guerreiro Mumu x1.'])
        novo = quadro(['Voce obteve 789 Kinah.', 'Voce obteve 987 Kinah.',
                      'Obteve Pontos da Alma Papis x1.',
                      'Obteve Pontos da Alma Papis x1.'])
        self.assertEqual(r.quadro_pos(antigo, 0), [])
        self.assertEqual(r.quadro_pos(novo, 1), [])
        self.assertEqual(r.quadro_pos(novo, 2), ['Papis', 'Papis'])
        self.assertEqual(r.quadro_pos(novo, 3), [])
        self.assertEqual(r.quadro_pos(antigo, 4), [])
        self.assertEqual(r.quadro_pos(antigo, 5), [])

    def test_pontos_do_abismo_nao_sao_lidos_pelo_chat(self):
        r = ca.Rastreador(ca.PADROES_PADRAO)
        quadro = lambda linhas: [(t, 100 + i * 30, 18) for i, t in enumerate(linhas)]
        self.assertEqual(r.quadro_pos(quadro(['Voce obteve 150 Pontos do Abismo']), 0), [])


if __name__ == '__main__':
    unittest.main()
