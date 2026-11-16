"""Página local do AionTrack para a Fonte de navegador do OBS."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 18742
_estado = {}
_servidor = None

PAGINA = '''<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<style>
*{box-sizing:border-box}body{margin:0;background:transparent;color:#e6eaf3;font:14px "Segoe UI",sans-serif;display:flex;flex-direction:column;align-items:center}
.barra{display:flex;align-items:center;gap:20px;width:max-content;max-width:100%;padding:8px 16px;background:rgba(23,28,41,.88);border-radius:12px}
.marca{color:#b7cfda;letter-spacing:1px}.icones{color:#9ca9bd;letter-spacing:18px}.ativo{color:#dabaff}.meta{font-size:12px;color:#a2e4d5}
.painel,.diarios,.aviso{width:max-content;min-width:370px;max-width:550px;margin-top:7px;padding:12px 14px;background:rgba(23,28,41,.90);border-radius:12px}
.painel[hidden],.diarios[hidden],.aviso[hidden]{display:none}.titulo{color:#dabaff;margin-bottom:9px}.linha{display:flex;justify-content:space-between;gap:20px;padding:5px 0;border-bottom:1px solid #2b3548}.linha:last-child{border:0}
.diarios{display:flex;gap:14px;min-width:0;max-width:100%;padding:5px 12px;background:rgba(23,28,41,.76);font-size:11px}.diarios[hidden]{display:none}.card{display:flex;align-items:center;gap:6px;white-space:nowrap;color:#a2e4d5}.card b{color:#bcc7d8;font-weight:400}
</style><div class="barra"><span class="marca">✦ AIONTRACK</span><span class="icones">♙ ⚔ ◈ ⚒ ▣ ◉</span><span id="char"></span><span class="meta" id="ping"></span><span id="hora"></span></div>
<div class="diarios" id="diarios" hidden></div><div class="painel" id="painel" hidden><div class="titulo" id="titulo"></div><div id="linhas"></div></div><div class="aviso" id="aviso" hidden></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function atualizar(){try{let d=await(await fetch('/estado',{cache:'no-store'})).json();
document.getElementById('char').textContent=d.personagem||'';document.getElementById('ping').textContent=d.ping||'';document.getElementById('hora').textContent=d.hora||'';
let p=document.getElementById('painel');p.hidden=!d.painel;document.getElementById('titulo').textContent=d.aba||'';
document.getElementById('linhas').innerHTML=(d.linhas||[]).map(x=>'<div class="linha"><span>'+esc(x[0])+'</span><b>'+esc(x[1])+'</b></div>').join('');
let f=document.getElementById('diarios');f.hidden=!d.diarios;f.innerHTML=(d.diarios||[]).map(x=>'<div class="card">'+esc(x[0])+'<b>'+esc(x[1])+'</b></div>').join('');
let a=document.getElementById('aviso');a.hidden=!d.aviso;a.textContent=d.aviso||'';
}catch(e){}}setInterval(atualizar,500);atualizar();</script></html>'''

def atualizar(estado):
    global _estado
    _estado = estado

def iniciar():
    global _servidor
    if _servidor is not None:
        return True
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/estado':
                corpo = json.dumps(_estado, ensure_ascii=False).encode('utf-8')
                tipo = 'application/json; charset=utf-8'
            elif self.path in ('/', '/index.html'):
                corpo = PAGINA.encode('utf-8')
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
