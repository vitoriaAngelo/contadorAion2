"""Página local do AionTrack para a Fonte de navegador do OBS."""
import json
import base64
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 18742
_estado = {}
_servidor = None

PAGINA = '''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{--fundo:rgba(15,22,34,.92);--barra:rgba(20,27,40,.91);--linha:#2b3548;--texto:#e6eaf3;--suave:#9099ac;--menta:#a2e4d5;--lilas:#dabaff}
*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent;color:var(--texto);font:13px "Segoe UI",Arial,sans-serif}
body{display:flex;justify-content:center;align-items:flex-start;padding-top:8px}.overlay{display:flex;flex-direction:column;align-items:center;min-width:560px}
.barra{height:38px;display:flex;align-items:center;gap:7px;padding:4px 8px 4px 10px;background:var(--barra);border-radius:11px;box-shadow:0 5px 18px rgba(0,0,0,.25)}
.marca{display:flex;align-items:center;gap:7px;padding-right:8px;color:#b7cfda;letter-spacing:1.6px;font-size:11px;white-space:nowrap}.logo{color:var(--menta);font-size:15px}
.atalho{width:36px;height:29px;display:grid;place-items:center;border-radius:7px;color:var(--suave);font-size:15px}.atalho.ativo{background:#30364a;color:var(--cor)}
.separador{width:1px;height:18px;background:#495066;margin:0 2px}.char{color:var(--lilas);margin-left:4px;white-space:nowrap}.meta{color:var(--menta);font-size:11px;white-space:nowrap}.hora{color:#9fc9ff;font-size:11px;white-space:nowrap}
.aviso,.diarios,.painel{margin-top:6px;background:var(--fundo);border-radius:10px;box-shadow:0 7px 22px rgba(0,0,0,.28)}
.painel{width:430px;max-height:560px;overflow:hidden;padding:10px 12px}.painel[hidden],.diarios[hidden],.aviso[hidden]{display:none!important}
.titulo{color:var(--cor,var(--lilas));font-weight:600;font-size:12px;margin-bottom:7px;padding-bottom:7px;border-bottom:1px solid var(--linha)}
.vazio{color:var(--suave);padding:14px 2px;text-align:center}.linha{position:relative;display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:30px;padding:6px 7px;border-radius:6px;overflow:hidden}
.linha:nth-child(odd){background:rgba(37,47,65,.38)}.linha span,.linha b{position:relative;z-index:1}.linha span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.linha b{font-weight:600;color:#dce5f3;text-align:right;white-space:nowrap}
.diarios{display:flex;gap:12px;padding:5px 10px;background:rgba(20,27,40,.82);font-size:11px}.card{display:flex;gap:5px;white-space:nowrap;color:var(--menta)}.card b{color:#c7cfdd;font-weight:400}
.aviso{min-width:330px;padding:8px 12px;color:#f3d4a1;text-align:center}
</style></head><body><main class="overlay"><div class="barra"><span class="marca"><b class="logo">✦</b>AIONTRACK</span>
<span class="atalho" data-aba="Pets" style="--cor:#dabaff">✦</span><span class="atalho" data-aba="DPS" style="--cor:#ffbdce">⚔</span><span class="atalho" data-aba="Abyss" style="--cor:#abcaff">◆</span><span class="atalho" data-aba="Materiais" style="--cor:#a2e4d5">✧</span><span class="atalho" data-aba="FARM" style="--cor:#f3d4a1">▣</span>
<span class="separador"></span><span class="char" id="char"></span><span class="meta" id="ping"></span><span class="hora" id="hora"></span></div>
<div class="aviso" id="aviso" hidden></div><div class="diarios" id="diarios" hidden></div><section class="painel" id="painel" hidden><div class="titulo" id="titulo"></div><div id="linhas"></div></section></main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cores={Pets:'#dabaff',DPS:'#ffbdce',Abyss:'#abcaff',Materiais:'#a2e4d5',FARM:'#f3d4a1'};
const extras=document.createElement('div');extras.style.cssText='display:flex;gap:8px;align-items:flex-start';document.querySelector('.overlay').appendChild(extras);
const mapa=document.createElement('img');mapa.style.cssText='width:280px;height:280px;border-radius:10px;margin-top:6px';extras.appendChild(mapa);
const loja=document.createElement('section');loja.className='painel';extras.appendChild(loja);
async function atualizar(){try{const r=await fetch('/estado?'+Date.now(),{cache:'no-store'});const d=await r.json();
mapa.hidden=!d.mapa;if(d.mapa&&mapa.getAttribute('src')!==d.mapa)mapa.src=d.mapa;
loja.hidden=!(d.loja&&d.loja.length);loja.innerHTML='<div class="titulo">BLACK CLOUD · PETS</div>'+(d.loja||[]).map(x=>'<div class="linha"><span>'+esc(x[0])+'</span><b>'+esc(x[1])+'</b></div>').join('');
char.textContent=d.personagem||'';ping.textContent=d.ping||'';hora.textContent=d.hora||'';
document.querySelectorAll('.atalho').forEach(x=>x.classList.toggle('ativo',!!d.painel&&x.dataset.aba===d.aba));
const p=document.getElementById('painel');p.hidden=!d.painel;p.style.setProperty('--cor',cores[d.aba]||'#dabaff');titulo.textContent=d.aba||'';
const itens=d.linhas||[];linhas.innerHTML=itens.length?itens.map(x=>'<div class="linha"><span>'+esc(x[0])+'</span><b>'+esc(x[1])+'</b></div>').join(''):'<div class="vazio">Aguardando informações…</div>';
const f=document.getElementById('diarios');f.hidden=!(d.diarios&&d.diarios.length);f.innerHTML=(d.diarios||[]).map(x=>'<div class="card">'+esc(x[0])+' <b>'+esc(x[1])+'</b></div>').join('');
const a=document.getElementById('aviso');a.hidden=!d.aviso;a.textContent=d.aviso||'';
}catch(e){}}setInterval(atualizar,350);atualizar();</script></body></html>'''

PAGINA_CAPTURA = '''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
html,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent}
body{position:relative}#janelas{position:absolute;inset:0}img{position:absolute;display:block}
</style></head><body><main id="janelas"></main><script>
let anterior='';async function atualizar(){try{const r=await fetch('/estado?'+Date.now(),{cache:'no-store'});
const d=await r.json(), j=d.janelas||[];if(!j.length){janelas.replaceChildren();anterior='';return}
const chave=j.map(x=>x.nome+':'+x.x+':'+x.y+':'+x.w+':'+x.h).join('|');
if(chave!==anterior){janelas.replaceChildren();for(const x of j){const im=document.createElement('img');
im.dataset.nome=x.nome;janelas.appendChild(im)}anterior=chave}
for(const x of j){const im=[...janelas.children].find(v=>v.dataset.nome===x.nome);
im.style.left=x.x+'px';im.style.top=x.y+'px';im.width=x.w;im.height=x.h;
if(im.dataset.imagem!==x.imagem){im.src=x.imagem;im.dataset.imagem=x.imagem}}
}catch(e){}}setInterval(atualizar,400);atualizar();</script></body></html>'''

def capturar_janelas(janelas):
    """Lê somente as janelas do AionTrack, sem capturar o jogo."""
    import ctypes
    from PIL import ImageGrab
    from ctypes import wintypes
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
    usuario = ctypes.windll.user32
    usuario.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    usuario.MonitorFromWindow.restype = wintypes.HANDLE
    usuario.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    usuario.GetMonitorInfoW.restype = wintypes.BOOL
    origem_x = origem_y = 0
    referencia = next((janela for nome, janela in janelas if nome == 'barra' and janela is not None), None)
    if referencia is not None and referencia.winfo_exists():
        monitor = usuario.MonitorFromWindow(referencia.winfo_id(), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if monitor and usuario.GetMonitorInfoW(monitor, ctypes.byref(info)):
            origem_x, origem_y = info.rcMonitor.left, info.rcMonitor.top
    visiveis = []
    for nome, janela in janelas:
        if janela is None:
            continue
        try:
            if not janela.winfo_exists() or not janela.winfo_ismapped():
                continue
            imagem = ImageGrab.grab(window=janela.winfo_id())
            if imagem.width < 2 or imagem.height < 2:
                continue
            saida = io.BytesIO()
            imagem.save(saida, format='PNG')
            visiveis.append({'nome': nome, 'x': janela.winfo_rootx() - origem_x,
                             'y': janela.winfo_rooty() - origem_y, 'w': imagem.width,
                             'h': imagem.height,
                             'imagem': 'data:image/png;base64,' +
                             base64.b64encode(saida.getvalue()).decode('ascii')})
        except (OSError, ValueError, AttributeError):
            continue
    if not visiveis:
        return {'janelas': [], 'largura': 0, 'altura': 0}
    return {'janelas': visiveis}

def atualizar(estado):
    global _estado
    _estado = estado

def iniciar():
    global _servidor
    if _servidor is not None:
        return True
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.path = self.path.split('?', 1)[0]
            if self.path == '/estado':
                corpo = json.dumps(_estado, ensure_ascii=False).encode('utf-8')
                tipo = 'application/json; charset=utf-8'
            elif self.path in ('/', '/index.html'):
                corpo = PAGINA_CAPTURA.encode('utf-8')
                tipo = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', tipo)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        def log_message(self, *_):
            pass
    try:
        _servidor = ThreadingHTTPServer(('127.0.0.1', PORTA), Handler)
    except OSError:
        return False
    threading.Thread(target=_servidor.serve_forever, daemon=True, name='AionTrack OBS').start()
    return True
