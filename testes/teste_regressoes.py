import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca


class Regressoes(unittest.TestCase):
    def test_nomes_das_almas_respeitam_o_idioma(self):
        self.assertEqual(ca.carregar_nomes_pets('en')['1001'], 'Black Dragon')
        self.assertEqual(ca.carregar_nomes_pets('pt-BR')['1001'], 'Dragão Negro')

    def test_cores_do_dps_nao_repetem_com_classes_iguais(self):
        classes = {'a': 'Chanter', 'b': 'Chanter', 'c': 'Chanter', 'd': '', 'e': 'Cleric'}
        cores = ca.cores_dps(list(classes), classes)
        self.assertEqual(len(set(cores.values())), 5)
        self.assertEqual(cores['a'], ca.CORES_DPS_CLASSE['Chanter'])

    def test_barra_do_dps_e_relativa_ao_maior_dano(self):
        self.assertEqual(ca.fracoes_dps(['a', 'b'], {'a': 200, 'b': 50}), {'a': 1.0, 'b': 0.25})
        self.assertEqual(ca.fracoes_dps(['a'], {}), {'a': 0.0})

    def test_barra_do_dps_desliza_ate_o_alvo(self):
        passo = ca.avancar_barra(0.0, 1.0)
        self.assertTrue(0.0 < passo < 1.0)
        atual = 0.0
        for _ in range(80):
            atual = ca.avancar_barra(atual, 1.0)
        self.assertEqual(atual, 1.0)

    def test_aba_automatica_vai_para_a_atividade(self):
        aba = ca.aba_automatica
        self.assertEqual(aba('almas', 'boss', 100, -1e9, {}, True), 'dps')
        self.assertEqual(aba('almas', 'materiais', 100, -1e9, {}, False), 'materiais')
        self.assertEqual(aba('dps', 'alma', 100, -1e9, {'dps': 50}, False), 'almas')
        self.assertIsNone(aba('od', 'materiais', 100, -1e9, {}, False))

    def test_aba_automatica_nao_sai_do_boss_nem_de_aba_ativa(self):
        aba = ca.aba_automatica
        self.assertIsNone(aba('dps', 'alma', 100, -1e9, {'dps': 99}, True))
        self.assertIsNone(aba('dps', 'od', 100, -1e9, {'dps': 99}, True))
        self.assertIsNone(aba('materiais', 'alma', 100, -1e9, {'coleta': 90}, False))
        self.assertEqual(aba('materiais', 'alma', 100, -1e9, {'coleta': 84}, False), 'almas')

    def test_aba_automatica_respeita_troca_manual_e_outras_telas(self):
        aba = ca.aba_automatica
        self.assertIsNone(aba('almas', 'boss', 100, 85, {}, True))
        self.assertEqual(aba('almas', 'boss', 100, 79, {}, True), 'dps')
        self.assertIsNone(aba('info', 'boss', 100, -1e9, {}, True))
        self.assertIsNone(aba('pets', 'alma', 100, -1e9, {}, False))

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

    def _atualizar_com_requirements(self, local, remoto):
        with tempfile.TemporaryDirectory() as pasta:
            os.mkdir(os.path.join(pasta, 'psutil'))
            for nome, dados in [('requirements.txt', local), ('contador_almas.py', b'')]:
                with open(os.path.join(pasta, nome), 'wb') as f:
                    f.write(dados)
            with mock.patch.object(ca, 'baixar_texto',
                                   lambda url: remoto if 'requirements' in url else b''):
                return ca.aplicar_atualizacao(b'', pasta)

    def test_quebra_de_linha_nao_conta_como_requirements_novo(self):
        self.assertFalse(self._atualizar_com_requirements(b'mss>=9.0\r\npsutil>=6.0\r\n',
                                                          b'mss>=9.0\npsutil>=6.0\n'))

    def test_requirements_novo_pede_instalacao(self):
        self.assertTrue(self._atualizar_com_requirements(b'mss>=9.0\n', b'mss>=9.0\npsutil>=6.0\n'))

    def test_abertura_repara_so_bibliotecas_instaladas_na_pasta(self):
        with tempfile.TemporaryDirectory() as pasta:
            with mock.patch.object(ca, 'bibliotecas_ok', return_value=False), \
                    mock.patch.object(ca, 'instalar_bibliotecas', return_value=True) as pip:
                ca.reparar_bibliotecas(pasta)  # RODAR.bat: nada instalado aqui
                pip.assert_not_called()
                os.mkdir(os.path.join(pasta, 'psutil-7.2.2.dist-info'))
                ca.reparar_bibliotecas(pasta)
                pip.assert_called_once_with(pasta)

    def test_abertura_nao_chama_pip_com_bibliotecas_inteiras(self):
        with tempfile.TemporaryDirectory() as pasta:
            os.mkdir(os.path.join(pasta, 'psutil-7.2.2.dist-info'))
            with mock.patch.object(ca, 'bibliotecas_ok', return_value=True), \
                    mock.patch.object(ca, 'instalar_bibliotecas') as pip:
                ca.reparar_bibliotecas(pasta)
                pip.assert_not_called()
                ca.reparar_bibliotecas(pasta, forcar=True)
                pip.assert_called_once_with(pasta)

    def test_erro_de_import_nao_culpa_o_npcap(self):
        erro = "cannot import name '_common' from partially initialized module 'psutil'"
        self.assertNotIn('Npcap', ca.dica_erro_leitura(erro, 'npcap'))
        self.assertIn('Npcap', ca.dica_erro_leitura('Npcap ausente', 'npcap'))


if __name__ == '__main__':
    unittest.main()
