#!/usr/bin/env python3
"""Insere UI do controle manual preservando o HTML existente do usuario.
Uso: python aplicar_no_index.py caminho/para/index.html
"""
from pathlib import Path
import sys

CSS = r'''
/* JOJO Refresh Manual: status publico e envio seguro da chave somente ao HTTPS da Oracle */
.monitorManualArea{margin-top:13px;padding-top:12px;border-top:1px solid #4c3b61;display:flex;flex-wrap:wrap;align-items:center;gap:9px}
.monitorManualBtn{background:linear-gradient(135deg,#8151b9,#5b3485);border:1px solid #af83de;border-radius:11px;padding:11px 16px;color:#fff;font-size:13px;font-weight:850}
.monitorManualBtn:disabled{opacity:.55;cursor:not-allowed;filter:grayscale(.35)}
.monitorManualInfo{font-size:12px;color:#c4b4d3;line-height:1.5;flex:1 1 250px}
.monitorAuthDialog{width:min(94vw,420px);padding:0;background:#1e162b;color:#f6edff;border:1px solid #8d65b8;border-radius:17px;box-shadow:0 20px 90px #0009}
.monitorAuthDialog::backdrop{background:#06040aca;backdrop-filter:blur(4px)}
.monitorAuthInner{padding:23px}
.monitorAuthInner h3{margin:0 0 7px;font-size:20px}
.monitorAuthInner p{margin:0 0 14px;font-size:13px;color:#b6a7c8}
.monitorAuthInner input{width:100%;background:#100c1a;border:1px solid #775a93;color:white;padding:13px 14px;border-radius:10px;margin-bottom:12px}
.monitorAuthButtons{display:flex;gap:9px}
.monitorAuthButtons button{flex:1;border-radius:10px;padding:11px 10px;background:#5e3b86;border:1px solid #9b76bf;font-weight:750}
.monitorAuthButtons button[type=button]{background:#2e253b;border-color:#625172}
.monitorAuthFeedback{min-height:18px;margin:10px 0 0!important;color:#dec5ff!important}
@media(max-width:570px){.monitorManualBtn{width:100%}.monitorManualInfo{flex-basis:100%}}
'''
HTML = r'''
<div class="monitorManualArea">
<button type="button" id="monitorManual" class="monitorManualBtn" disabled>🔄 Solicitar atualização</button>
<div id="monitorManualInfo" class="monitorManualInfo" aria-live="polite">Carregando controle de atualização manual…</div>
</div>
'''
DIALOG = r'''
<dialog id="monitorAuthDialog" class="monitorAuthDialog" aria-label="Solicitar atualização do OK.ru">
<form id="monitorAuthForm" class="monitorAuthInner">
<h3>🔄 Atualizar OK.ru agora</h3>
<p>A Oracle valida a chave e o limite global: até 3 solicitações por dia, com 90 minutos de intervalo. A chave não é salva no navegador.</p>
<label for="monitorKey">Chave administrativa</label>
<input id="monitorKey" type="password" autocomplete="off" required minlength="6" maxlength="128" placeholder="Digite sua chave">
<div class="monitorAuthButtons"><button type="submit" id="monitorAuthSend">Solicitar</button><button type="button" id="monitorAuthCancel">Cancelar</button></div>
<p id="monitorAuthFeedback" class="monitorAuthFeedback" role="status"></p>
</form>
</dialog>
'''
JS = r'''
function initMonitorManual(){
 var btn=$('monitorManual'),info=$('monitorManualInfo'),dialog=$('monitorAuthDialog'),key=$('monitorKey');
 var send=$('monitorAuthSend'),feedback=$('monitorAuthFeedback'),base='';
 function message(s){txt(info,s)}
 function clock(sec){var m=Math.ceil(Math.max(0,Number(sec)||0)/60);return m>=60?Math.floor(m/60)+'h '+(m%60)+'min':m+'min'}
 function loadStatus(){
  if(!base){btn.disabled=true;message('API segura ainda não conectada. Configure o endereço HTTPS da Oracle.');return Promise.resolve()}
  return fetch(base+'/api/jojo-monitor/status',{cache:'no-store',credentials:'omit'}).then(function(r){if(!r.ok)throw Error('API indisponível');return r.json()}).then(function(d){
   var t='Automático: 6h · Manuais: '+Number(d.usedToday||0)+'/3 hoje';
   if(d.running)t+=' · Verificação em andamento';
   else if(d.queued)t+=' · Na fila';
   else if(d.remainingToday===0)t+=' · Limite de hoje atingido';
   else if(d.cooldownSeconds>0)t+=' · Liberado em '+clock(d.cooldownSeconds);
   else t+=' · Pronto para solicitar';
   btn.disabled=!d.canRequest;message(t);
  }).catch(function(){btn.disabled=true;message('Oracle indisponível. A rotina automática continua independente do botão.')});
 }
 function refresh(){return fetch('./config/monitor_api.json?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw Error('Configuração não encontrada');return r.json()}).then(function(d){
  var s=String(d.apiBase||'').trim().replace(/\/+$/,'');
  if(!/^https:\/\/[^/\s?#]+$/i.test(s))throw Error('Sem API HTTPS');
  base=s;return loadStatus();
 }).catch(function(){base='';btn.disabled=true;message('Atualização manual indisponível até configurar o HTTPS da Oracle.')});}
 btn.addEventListener('click',function(){if(btn.disabled||!base)return;txt(feedback,'');key.value='';dialog.showModal();key.focus()});
 $('monitorAuthCancel').addEventListener('click',function(){key.value='';dialog.close()});
 dialog.addEventListener('close',function(){key.value='';txt(feedback,'')});
 $('monitorAuthForm').addEventListener('submit',function(e){
  e.preventDefault();if(!base)return;var secret=key.value;send.disabled=true;txt(feedback,'Solicitando à Oracle…');
  fetch(base+'/api/jojo-monitor/request',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'omit',cache:'no-store',body:JSON.stringify({key:secret})})
  .then(function(r){return r.json().catch(function(){return {message:'Resposta inválida da Oracle'}}).then(function(d){return {ok:r.ok,data:d}})})
  .then(function(result){txt(feedback,result.data.message||'Resposta recebida');key.value='';if(result.ok){dialog.close();message('Solicitação aceita. O monitor será executado pela Oracle.');setTimeout(function(){loadStatus();loadMonitorStatus()},1800)}else{loadStatus()}})
  .catch(function(){key.value='';txt(feedback,'Erro de conexão. Verifique o endereço HTTPS da Oracle.');loadStatus()})
  .finally(function(){send.disabled=false});
 });
 $('monitorRefresh').addEventListener('click',loadStatus);
 refresh();
}
'''


def patch_html(path):
    text = path.read_text(encoding='utf-8')
    if 'id="monitorAuthDialog"' in text:
        print('Controle manual já está neste index.html; não alterei.')
        return False
    anchors = {
        '</style>': CSS + '\n</style>',
        '<div class="monitorProfiles" id="monitorProfiles"></div>': '<div class="monitorProfiles" id="monitorProfiles"></div>\n'+HTML,
        '<div id="shade"': DIALOG + '\n<div id="shade"',
        'function loadMonitorStatus(){': JS + '\nfunction loadMonitorStatus(){',
        'loadMonitorStatus();loadTrustedSites();': 'loadMonitorStatus();loadTrustedSites();initMonitorManual();',
    }
    for old, new in anchors.items():
        if text.count(old) != 1:
            raise SystemExit('HTML incompatível: esperado 1 marcador '+old[:65]+'; encontrados '+str(text.count(old)))
        text = text.replace(old,new,1)
    backup = path.with_suffix(path.suffix+'.antes-refresh')
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_text(text,encoding='utf-8')
    print('Index atualizado. Backup:',backup)
    return True


if __name__ == '__main__':
    if len(sys.argv)!=2: raise SystemExit('Uso: python aplicar_no_index.py index.html')
    patch_html(Path(sys.argv[1]))
