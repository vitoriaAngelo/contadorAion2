# ✦ Contador de Almas — Aion 2

Overlay para **Aion 2** que conta sozinho as almas de cada monstro que você farma e mostra o progresso de cada estrela (5 → 25 → 75 almas), sem precisar abrir o menu de monstros.

![Overlay](docs/overlay.png)

## Como funciona

O contador **lê o chat do jogo pela tela** (como uma captura do OBS). Quando aparece
`Obteve Pontos da Alma <monstro> x1.`, ele soma +1 naquele monstro.

- **Não** lê a memória do jogo, **não** mexe na rede e **não** pede login.
- Acha o chat sozinho, aguenta matança em massa (AoE), chat aberto/fechado e menu por cima.
- Aparece na stream (OBS / Discord) — dá para usar como fonte de **Captura de Janela**.

> ⚠️ **Aviso:** é um programa de terceiros, não oficial. Ele só olha a tela, mas nenhum programa externo tem garantia contra o anti-cheat do jogo. Use por sua conta e risco.

## Instalar

1. Baixe o projeto: em <https://github.com/vitoriaAngelo/contadorAion2>, botão verde **Code → Download ZIP**, e extraia em qualquer pasta.
2. Dê dois cliques em **`INSTALAR.bat`**.
   - Se o Windows mostrar "O Windows protegeu o computador", clique em **Mais informações → Executar assim mesmo**.
3. Espere terminar. Ele instala o que faltar sozinho:
   - **Python** (se você não tiver);
   - **Tesseract** (o leitor de texto da tela) — o Windows pode pedir permissão de administrador;
   - o **pacote de português** do leitor.
4. Pronto: use o ícone **Contador de Almas** na Área de Trabalho (também fica no Menu Iniciar).

Depois de instalado, a pasta que você extraiu pode ser apagada.

## Usar

- Abra o jogo em **Janela sem bordas** (em tela cheia exclusiva o overlay não aparece por cima).
- Abra o contador. Ele procura o chat sozinho (deixe algumas linhas "Você obteve…" visíveis).
- Mate monstros: cada alma soma no monstro certo.

Em cada monstro:

| | |
|---|---|
| ★★☆ | estrelas: douradas = completas, contorno dourado = a que você está fazendo. **Clique numa estrela** para dizer em qual você está. |
| `71/75` | almas da estrela atual |
| **tenho** [ ] | digite quantas almas você já tem **naquela estrela** e aperte Enter |
| − + | corrigir na mão |
| ✕ | tirar o monstro da lista |

Exemplo: no jogo você está na **3ª estrela** e tem **70**. Clique na 3ª estrela, digite **70**, Enter → `70/75`.

Menu ⚙: detectar o chat de novo, escolher a área na mão, opacidade, mostrar concluídos, aparecer ou não na stream.

## OBS

Fonte **Captura de Janela** → janela **[pythonw.exe]: Contador de Almas** → método de captura **Windows 10 (1903 e superior)**.

## Desinstalar

**Configurações → Aplicativos → Contador de Almas → Desinstalar.**
Ele pergunta se você quer guardar uma cópia do seu progresso na Área de Trabalho.
(O Python e o Tesseract continuam instalados — dá para tirar pela mesma tela, se quiser.)

## Problemas

- **"Não consegui iniciar o OCR"** → o Tesseract não está instalado. Instale por <https://github.com/UB-Mannheim/tesseract/wiki> e abra o contador de novo.
- **Não conta nada** → ⚙ → *Detectar o chat automaticamente*, com o chat visível. Se não achar, ⚙ → *Selecionar área do chat na mão*.
- **Contou errado** → use − / + ou a caixinha **tenho**.
- O programa grava `diagnostico.log` e `captura_*.png` na pasta de instalação (`%LOCALAPPDATA%\ContadorDeAlmas`). Ajudam a entender o que aconteceu.

## Requisitos

- Windows 10 ou 11
- Aion 2 em **português** (os textos em inglês também são reconhecidos, mas não foram testados no jogo)

## Para quem quer mexer no código

```
pip install -r requirements.txt
RODAR.bat        (ou: pythonw contador_almas.py)
```

Tudo fica em `contador_almas.py` (Python + tkinter). Licença MIT — veja `LICENSE`.
