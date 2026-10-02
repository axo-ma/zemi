"""Reloadable Codex report bridge with configuration-selected local actions."""
from pathlib import Path
import sys,json,os,subprocess,runpy
from urllib.parse import urlsplit, unquote
URI='ui://zemi/probe.html'
REPORT_URI='ui://zemi/report.html'
SETTINGS=json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
EXCEL_FILE=Path(SETTINGS['excel']) if SETTINGS.get('excel') else None
COMPONENT=Path(SETTINGS['component'])
CHAT_MANIFEST=Path(SETTINGS['manifest']) if SETTINGS.get('manifest') else None
CHAT_VIEWER=COMPONENT/'zemi/report_viewer.py'

def report_path(value):
    path=Path(value)
    if not path.is_absolute(): path=COMPONENT/path
    path=path.resolve()
    roots=[COMPONENT.resolve()]
    if CHAT_MANIFEST is not None: roots.append(CHAT_MANIFEST.parent.resolve())
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError('Report must be inside the configured component or selected run')
    if path.suffix.lower() not in {'.md','.html'} or not path.with_suffix('.md').is_file():
        raise ValueError('A report must have its Markdown source')
    return path.with_suffix('.html')

def report_html(path):
    viewer=Path(__file__).resolve().parents[2]/'report_viewer.py'
    renderer=runpy.run_path(str(viewer))['render_markdown']
    return renderer(path.with_suffix('.md'),bridge=False)

def report_action(arguments):
    from bs4 import BeautifulSoup
    report=report_path(arguments['report'])
    href=arguments['href']
    markup=report_html(report)
    links={a['href'] for a in BeautifulSoup(markup,'html.parser').find_all('a',href=True)}
    if href not in links: raise ValueError('This action is not a link in the selected report')
    if href.startswith('zemi-chat:'):
        from zemi.report_chat import load_context
        row,column=map(int,href.split(':')[1:])
        if row<0 or column<0: raise ValueError('Invalid cell address')
        manifest=report.with_suffix('.chat.json')
        load_context(manifest,row,column)
        command=[sys.executable,str(CHAT_VIEWER),'--chat',str(manifest),str(row),str(column)]
        process=subprocess.Popen(command,cwd=str(COMPONENT),creationflags=subprocess.CREATE_NEW_CONSOLE)
        return {'open_requested':True,'pid':process.pid,'item':row+1,'sample':column+1}
    url=urlsplit(href)
    if url.scheme not in {'','file'} or url.netloc:
        raise ValueError('Only local report and artifact links are supported')
    value=unquote(url.path)
    if url.scheme=='file' and len(value)>2 and value[0]=='/' and value[2]==':': value=value[1:]
    target=Path(value)
    if not target.is_absolute(): target=report.parent/target
    target=target.resolve()
    if target.suffix.lower() in {'.md','.html'}:
        target=report_path(target)
        return {'html':report_html(target),'report':str(target),'fragment':unquote(url.fragment)}
    if target.suffix.lower() not in {'.xlsx','.xls','.xlsm','.csv','.ipynb','.json','.pdf','.png','.txt'}:
        raise ValueError('Unsupported artifact type')
    if not target.is_file(): raise ValueError('Linked artifact does not exist')
    os.startfile(str(target))
    return {'open_requested':True,'file':str(target)}

def chat_command():
    if CHAT_MANIFEST is None or not CHAT_MANIFEST.is_file() or not CHAT_VIEWER.is_file():
        raise ValueError('Chat manifest or launcher does not exist')
    item=SETTINGS.get('item',1)
    sample=SETTINGS.get('sample',1)
    if type(item) is not int or type(sample) is not int or item<1 or sample<1:
        raise ValueError('Item and sample must be positive integers')
    manifest=json.loads(CHAT_MANIFEST.read_text(encoding='utf-8'))
    manifest['rows'][item-1]['cells'][sample-1]
    return [sys.executable,str(CHAT_VIEWER),'--chat',str(CHAT_MANIFEST),str(item-1),str(sample-1)]
def handle(method,params):
    if method=='initialize':
        return {'protocolVersion':params.get('protocolVersion','2025-06-18'),'capabilities':{'tools':{},'resources':{}},'serverInfo':{'name':'zemi-bridge-probe','version':'0.1.0'}}
    if method=='ping': return {}
    if method=='tools/list':
        return {'tools':[
            {'name':'zemi_report_show','description':'Show a ZEMI module, sample or dataset item report as an interactive MCP App. Report path is relative to the configured component or absolute within it.','inputSchema':{'type':'object','properties':{'report':{'type':'string'}},'required':['report'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False},'_meta':{'ui':{'resourceUri':REPORT_URI}}},
            {'name':'zemi_report_action','description':'Follow a local link from the displayed ZEMI report: open an Excel workbook, a terminal chat or another report.','inputSchema':{'type':'object','properties':{'report':{'type':'string'},'href':{'type':'string'}},'required':['report','href'],'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':False},'_meta':{'ui':{'visibility':['app','model']}}},
            {'name':'zemi_probe_open_chat','description':'Open a terminal model conversation for the item/sample selected in the ZEMI integration configuration','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':False,'openWorldHint':False},'_meta':{'ui':{'visibility':['app','model']}}},
            {'name':'zemi_probe_open_excel','description':'Open the workbook selected in the ZEMI integration configuration using the Windows file association','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':False,'openWorldHint':False},'_meta':{'ui':{'visibility':['app','model']}}},
            {'name':'zemi_probe_show','description':'Show the HTML to MCP tool bridge probe','inputSchema':{'type':'object','properties':{}},'annotations':{'readOnlyHint':True},'_meta':{'ui':{'resourceUri':URI}}},
            {'name':'zemi_probe_ping','description':'Return a test message without performing actions','inputSchema':{'type':'object','properties':{'message':{'type':'string'}},'required':['message'],'additionalProperties':False},'annotations':{'readOnlyHint':True},'_meta':{'ui':{'visibility':['app','model']}}}]}
    if method=='tools/call':
        if params['name']=='zemi_report_show':
            selected=report_path(params.get('arguments',{})['report'])
            report_html(selected)
            settings={**SETTINGS,'report':str(selected)}
            temporary=CONFIG_PATH.with_name('.'+CONFIG_PATH.name+'.tmp')
            temporary.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,CONFIG_PATH)
            result={'ready':True,'report':str(selected)}
        elif params['name']=='zemi_report_action':
            result=report_action(params.get('arguments',{}))
        elif params['name']=='zemi_probe_open_chat':
            if params.get('arguments',{}): raise ValueError('This action takes no arguments')
            process=subprocess.Popen(chat_command(),cwd=str(COMPONENT),creationflags=subprocess.CREATE_NEW_CONSOLE,stdin=None,stdout=None,stderr=None)
            result={'open_requested':True,'pid':process.pid,'item':SETTINGS.get('item',1),'sample':SETTINGS.get('sample',1),'note':'Process launched; check the terminal window for model startup'}
        elif params['name']=='zemi_probe_open_excel':
            if params.get('arguments',{}): raise ValueError('This action takes no arguments')
            if EXCEL_FILE is None or not EXCEL_FILE.is_file(): raise ValueError('Configure an existing Excel workbook first')
            os.startfile(str(EXCEL_FILE))
            result={'open_requested':True,'file':str(EXCEL_FILE),'note':'Windows accepted the request; verify the application window visually'}
        elif params['name']=='zemi_probe_ping':
            result={'pong':True,'message':params.get('arguments',{}).get('message','')}
        elif params['name']=='zemi_probe_show': result={'ready':True}
        else: raise ValueError('Unknown tool')
        return {'content':[{'type':'text','text':json.dumps(result)}],'structuredContent':result}
    if method=='resources/list': return {'resources':[{'uri':URI,'name':'ZEMI bridge probe','mimeType':'text/html;profile=mcp-app'},{'uri':REPORT_URI,'name':'ZEMI Report','mimeType':'text/html;profile=mcp-app'}]}
    if method=='resources/read' and params['uri']==REPORT_URI:
        if not SETTINGS.get('report'): raise ValueError('Call zemi_report_show first')
        return {'contents':[{'uri':REPORT_URI,'mimeType':'text/html;profile=mcp-app','text':report_html(report_path(SETTINGS['report'])),'_meta':{'ui':{'csp':{'connectDomains':[],'resourceDomains':[]}}}}]}
    if method=='resources/read' and params['uri']==URI:
        return {'contents':[{'uri':URI,'mimeType':'text/html;profile=mcp-app','text':Path(__file__).with_name('probe.html').read_text(encoding='utf-8'),'_meta':{'ui':{'csp':{'connectDomains':[],'resourceDomains':[]}}}}]}
    raise ValueError('Unsupported method')
if __name__=='__main__':
    for line in sys.stdin:
        request=json.loads(line)
        if 'id' not in request: continue
        try: response={'jsonrpc':'2.0','id':request['id'],'result':handle(request['method'],request.get('params',{}))}
        except Exception as error: response={'jsonrpc':'2.0','id':request['id'],'error':{'code':-32602,'message':str(error)}}
        print(json.dumps(response),flush=True)
