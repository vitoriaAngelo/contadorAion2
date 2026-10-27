import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca


class Regressoes(unittest.TestCase):
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

    def test_ganho_de_pontos_do_abismo_sem_recontar_chat(self):
        self.assertEqual(ca.pontos_abismo_em('Voce obteve 150 Pontos do Abismo'), 150)
        self.assertEqual(ca.pontos_abismo_em('+75 Abyss Points'), 75)
        self.assertEqual(ca.pontos_abismo_em('Abyss Points: 1,250'), 1250)
        self.assertEqual(ca.pontos_abismo_em('Pontos do Abismo obtidos 40'), 40)
        self.assertIsNone(ca.pontos_abismo_em('Voce obteve 463 Kinah'))

        r = ca.Rastreador(ca.PADROES_PADRAO)
        quadro = lambda linhas: [(t, 100 + i * 30, 18) for i, t in enumerate(linhas)]
        antigo = ['Voce obteve 123 Kinah.']
        primeiro = antigo + ['Voce obteve 150 Pontos do Abismo']
        segundo = primeiro + ['Voce obteve 124 Kinah.',
                              'Voce obteve 150 Pontos do Abismo']

        self.assertEqual(r.quadro_pos(quadro(antigo), 0), [])
        self.assertEqual(r.quadro_pos(quadro(primeiro), 1), ['§AP:150'])
        self.assertEqual(r.quadro_pos(quadro(primeiro), 2), [])
        self.assertEqual(r.quadro_pos(quadro(segundo), 3), ['§AP:150'])
        self.assertEqual(r.quadro_pos(quadro(segundo), 4), [])
        self.assertEqual(r.quadro_pos(quadro(primeiro), 5), [])


if __name__ == '__main__':
    unittest.main()
