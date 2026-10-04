"""Local Qt Markdown report viewer; no HTTP server or URL registration."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

REPORT_SCRIPT = r'''
(function(){
function fitSticky(){document.querySelectorAll('table.dataset-items').forEach(table=>{const item=table.querySelector('th.sticky-item');if(item)table.style.setProperty('--item-width',item.getBoundingClientRect().width+'px');});}
function fitTables(){document.querySelectorAll('.table-wrap').forEach(wrapper=>{const table=wrapper.querySelector('table');if(!table)return;const rows=[...table.querySelectorAll('tbody > tr')];if(rows.length<=10){wrapper.style.maxHeight='none';return;}const header=table.querySelector('thead');const height=(header?.getBoundingClientRect().height||0)+rows.slice(0,10).reduce((total,row)=>total+row.getBoundingClientRect().height,0)+2;wrapper.style.maxHeight=height+'px';});}
requestAnimationFrame(()=>{fitTables();fitSticky();});
window.addEventListener('resize',()=>{fitTables();fitSticky();});
if(window.qt && typeof QWebChannel==='function')new QWebChannel(qt.webChannelTransport,c=>{window.zemiBridge=c.objects.zemi;});
function status(message){let el=document.getElementById('zemi-action-status');if(!el){el=document.createElement('div');el.id='zemi-action-status';el.setAttribute('role','status');document.body.prepend(el);}el.textContent=message;}
document.addEventListener('click',async function(e){
const a=e.target.closest('a[href]');if(!a)return;
const href=a.getAttribute('href');if(href.startsWith('#'))return;
if(window.zemiBridge){e.preventDefault();e.stopPropagation();if(href.startsWith('zemi-chat:'))window.zemiBridge.openChat(href);else window.zemiBridge.openLink(a.href);return;}
if(typeof window.openai?.callTool!=='function'){if(href.startsWith('zemi-chat:')){e.preventDefault();status('Для чата откройте отчёт через CMD или Codex MCP.');}return;}
e.preventDefault();e.stopPropagation();
try{
status('Открываем…');
const result=await window.openai.callTool('zemi_report_action',{report:document.body.dataset.report,href});
let data=result?.structuredContent;
if(!data)for(const block of result?.content||[])if(block.type==='text')try{data=JSON.parse(block.text);}catch{}
if(result?.isError||!data)throw Error(data?.error||'Действие не подтверждено');
if(data.html){const parsed=new DOMParser().parseFromString(data.html,'text/html');document.body.innerHTML=parsed.body.innerHTML;document.body.dataset.report=parsed.body.dataset.report;const base=document.querySelector('base');if(base)base.href=parsed.querySelector('base').href;document.title=parsed.title;fitTables();fitSticky();const hash=data.fragment;if(hash)document.getElementById(hash)?.scrollIntoView();}
else if(data.open_requested)status('Запрос на открытие передан Windows.');
else throw Error(data.error||'Действие не подтверждено');
}catch(error){status('Ошибка: '+error.message);}
},true);
})();
'''

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'zemi'

ICON_PATH = Path(__file__).resolve().parent / 'assets' / 'dataset-report.ico'
WINDOWS_APP_ID = 'ZEMI.DatasetReport'

CSS = '''
body{margin:20px;background:#111314;color:#c6c9cc;font:14px/1.45 "Segoe UI",sans-serif}
h1{font-size:23px;color:#eee}h2{font-size:18px}a{color:#40b1d5;text-decoration:none}a:hover,a:focus-visible{text-decoration:underline;text-underline-offset:3px}
.table-wrap{overflow:auto;max-height:72vh;width:max-content;max-width:100%;box-sizing:border-box;border:1px solid #303538}
table{border-collapse:separate;border-spacing:0;font-size:13px;min-width:0;width:max-content}
table.dataset-items{min-width:0;width:max-content}
th,td{border-bottom:1px solid #393d40;padding:7px 10px;text-align:left;vertical-align:top;white-space:nowrap}
th{background:#191c1e;position:sticky;top:0;z-index:3;font-weight:600}
.module-samples .sample-number{box-sizing:border-box;width:38px;min-width:38px;padding:7px 4px;text-align:center}
.dataset-items .target-value,.dataset-items .item-value{display:inline-block;max-width:130px;overflow:hidden;text-overflow:ellipsis;vertical-align:top}
.dataset-items .sticky-item{position:sticky;left:0;box-sizing:border-box;width:150px;min-width:150px;max-width:150px;background:#111314;z-index:2}
.dataset-items .sticky-target{position:sticky;left:var(--item-width,150px);box-sizing:border-box;width:150px;min-width:150px;max-width:150px;background:#111314;z-index:2;overflow:hidden;text-overflow:ellipsis;box-shadow:2px 0 0 #393d40}
.dataset-items th.sticky-item,.dataset-items th.sticky-target{background:#191c1e;z-index:4}
.dataset-items .sticky-number{position:sticky;left:0;box-sizing:border-box;width:38px;min-width:38px;max-width:38px;padding:6px 4px;text-align:center;background:#111314;z-index:2}
.dataset-items th.sticky-number{background:#191c1e;z-index:4}
.dataset-items.numbered .sticky-item{left:38px}
.dataset-items.numbered .sticky-target{left:calc(38px + var(--item-width,150px))}
tbody tr:hover,tbody tr:hover td:first-child,tbody tr:hover td.sticky-target{background:#1b2023}
summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:450px;background:#202529;padding:10px;border-radius:4px;font:12px/1.5 Consolas,monospace}
details[open]{min-width:180px;max-width:450px}code{font-family:Consolas,monospace}
.run-error{color:#ef8181}.run-link{white-space:nowrap}
.best-sample,.best-sample a{color:#33dd88;font-weight:700}
'''


def render_markdown(path, *, content=None, bridge=True):
    from markdown_it import MarkdownIt
    path = Path(path).resolve()
    markup = MarkdownIt('commonmark', {'html': True}).enable('table').render(
        path.read_text(encoding='utf-8') if content is None else content)
    # Report HTML supports disclosure tags, but arbitrary scripts must not run
    # with a Python bridge. Remove executable content before adding our script.
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(markup, 'html.parser')
    import re
    heading_ids = set()
    for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        stem = re.sub(r'[^\w -]', '', heading.get_text().lower()).replace(' ', '-')
        identity = stem
        ordinal = 1
        while identity in heading_ids:
            identity = f'{stem}-{ordinal}'
            ordinal += 1
        heading_ids.add(identity)
        heading['id'] = identity
    for tag in soup.find_all(['script', 'iframe', 'object', 'embed', 'style', 'link', 'base', 'meta', 'form']):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith('on') or attr.lower() in {'srcdoc', 'style'}:
                del tag[attr]
        for attr in ('href', 'src'):
            if attr in tag.attrs and str(tag[attr]).strip().lower().startswith(('javascript:', 'data:', 'vbscript:')):
                del tag[attr]
    manifest = path.with_suffix('.chat.json')
    # Keep legacy saved Markdown readable with the current two-line metric layout.
    for table in soup.find_all('table'):
        for column, header in enumerate(table.select('thead th')):
            if not header.get_text().startswith('Metrics') or ' / ' not in header.get_text():
                continue
            for cell in [header, *(row.find_all('td', recursive=False)[column]
                                    for row in table.select('tbody > tr')
                                    if len(row.find_all('td', recursive=False)) > column)]:
                text = cell.get_text()
                if cell is header:
                    text = text.removeprefix('Metrics')
                parts = [part.strip() for part in text.split('/')]
                if len(parts) < 2:
                    continue
                middle = (len(parts) + 1) // 2
                cell.clear()
                if cell is header:
                    cell.append('Metrics')
                    cell.append(soup.new_tag('br'))
                cell.append(' / '.join(parts[:middle]) + ' /')
                cell.append(soup.new_tag('br'))
                cell.append(' / '.join(parts[middle:]))
    for candidate in soup.find_all('table'):
        names = [h.get_text().splitlines()[0] for h in candidate.select('thead th')]
        if names and names[0] == 'Sample' and 'Score' in names:
            candidate['class'] = [*candidate.get('class', []), 'module-samples']
            for row in candidate.select('thead > tr, tbody > tr'):
                first = row.find(['th', 'td'], recursive=False)
                if first:
                    first['class'] = [*first.get('class', []), 'sample-number']
    if soup.find('table'):  # Also recognize Items in a combined module report.
        table = next((t for t in soup.find_all('table')
                      if (lambda names: names and names[0] == 'Item ID' and
                          set(names[1:3]) == {'Matches', 'Target'})(
                              [h.get_text() for h in t.select('thead th') if h.get_text() != '#'])), None)
        if table:
            headers = table.select('thead th')
            offset = int(headers[0].get_text() == '#')
            if headers[1 + offset].get_text() == 'Matches':
                headers[1 + offset].insert_before(headers[2 + offset].extract())
                for row in table.select('tbody > tr'):
                    cells = row.find_all('td', recursive=False)
                    if len(cells) >= 3 + offset:
                        cells[1 + offset].insert_before(cells[2 + offset].extract())
            table['class'] = [*table.get('class', []), 'dataset-items']
            headers = table.select('thead th')
            if offset:
                table['class'].append('numbered')
                headers[0]['class'] = ['sticky-number']
                for row in table.select('tbody > tr'):
                    row.find_all('td', recursive=False)[0]['class'] = ['sticky-number']
            headers = headers[offset:]
            headers[0]['class'] = [*headers[0].get('class', []), 'sticky-item']
            headers[1]['class'] = [*headers[1].get('class', []), 'sticky-target']
            for row_index, row in enumerate(table.select('tbody > tr')):
                tds = row.find_all('td', recursive=False)
                tds = tds[offset:]
                if len(tds) >= 2:
                    tds[0]['class'] = [*tds[0].get('class', []), 'sticky-item']
                    tds[1]['class'] = [*tds[1].get('class', []), 'sticky-target']
                    tds[0]['title'] = tds[0].get_text()
                    item_value = soup.new_tag('span', attrs={'class': 'item-value'})
                    for child in list(tds[0].contents):
                        item_value.append(child.extract())
                    tds[0].append(item_value)
                    tds[1]['title'] = tds[1].get_text()
                    target_value = soup.new_tag('span', attrs={'class': 'target-value'})
                    for child in list(tds[1].contents):
                        target_value.append(child.extract())
                    tds[1].append(target_value)
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding='utf-8'))
                for row_index, row in enumerate(table.select('tbody > tr')):
                    tds = row.find_all('td', recursive=False)
                    tds = tds[offset:]
                    if row_index >= len(data['rows']) or not tds or tds[0].get_text() != data['rows'][row_index]['item_id']:
                        continue
                    for column, td in enumerate(tds[3:]):
                        try:
                            cell = data['rows'][row_index]['cells'][column]
                        except IndexError:
                            continue
                        contexts = cell.get('contexts', [])
                        if len(contexts) != 1 or not contexts[0].get('source') or contexts[0].get('unsupported_fields'):
                            continue
                        target = td.find('summary') or td
                        anchor = soup.new_tag('a', href=f'zemi-chat:{row_index}:{column}')
                        anchor['class'] = 'run-link' + (' run-error' if target.get_text().startswith('Error') else '')
                        anchor['title'] = 'Продолжить в терминале'
                        if target.name == 'summary' and 'run-error' in target.get('class', []):
                            anchor.string = 'Продолжить в терминале'
                            target.parent.append(anchor)
                            continue
                        for child in list(target.contents):
                            anchor.append(child.extract())
                        target.append(anchor)
    from urllib.parse import urlsplit, urlunsplit
    for anchor in soup.find_all('a', href=True):
        address = urlsplit(anchor['href'])
        if address.path.lower().endswith('.md') and address.scheme in {'', 'file'}:
            anchor['href'] = urlunsplit(address._replace(path=address.path[:-3] + '.html'))
    for table in soup.find_all('table'):
        wrapper = soup.new_tag('div', attrs={'class': 'table-wrap'})
        table.wrap(wrapper)
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; script-src \'unsafe-inline\' qrc:; img-src file:;">'
            f'<base href="{html.escape(path.parent.as_uri() + "/", quote=True)}">'
            f'<title>{html.escape(path.stem)}</title><style>{CSS}</style></head>'
            f'<body data-report="{html.escape(str(path.with_suffix(".html")), quote=True)}">{soup}' +
            '<script>if(window.qt)document.write(\'<script src="qrc:///qtwebchannel/qwebchannel.js"><\\/script>\');</script>'
            f'<script>{REPORT_SCRIPT}</script></body></html>')


def write_inline_report(report, destination):
    """Export a ready HTML report as a scoped fragment for embedding in chat."""
    import re
    from bs4 import BeautifulSoup
    report, destination = Path(report), Path(destination)
    soup = BeautifulSoup(report.read_text(encoding='utf-8'), 'html.parser')
    import hashlib
    root_id = 'zemi-module-' + hashlib.sha256(str(report.resolve()).encode('utf-8')).hexdigest()[:12]
    style = soup.find('style').get_text()
    def scope(match):
        selectors = match.group(1).strip()
        return ','.join('#' + root_id if s.strip() == 'body' else
                        '#' + root_id + ' ' + s.strip() for s in selectors.split(',')) + '{'
    style = re.sub(r'([^{}]+)\{', scope, style)
    style = style.replace('margin:20px;', 'margin:0;padding:20px;').replace('max-height:72vh;', '')
    for tag in soup.find_all('script'):
        tag.decompose()
    for a in soup.find_all('a', href=True):
        if not a['href'].startswith(('https://', '#')):
            del a['href']
    for tag in soup.find_all(title=True):
        tag['data-tooltip'] = tag.attrs.pop('title')
    for summary in soup.find_all('summary'):
        summary['class'] = [*summary.get('class', []), 'cursor-interaction']
    style = style.replace('cursor:pointer', 'cursor:inherit')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + destination.name + '.tmp')
    temporary.write_text(f'<style>{style}</style><section id="{root_id}">{soup.body.decode_contents()}</section>', encoding='utf-8')
    os.replace(temporary, destination)
    return destination


def write_launcher(report):
    """Launch the project interpreter and this library checkout, without a console."""
    report = Path(report)
    python = Path(sys.executable)
    pythonw = python.with_name('pythonw.exe')
    if pythonw.is_file():
        python = pythonw
    viewer = Path(__file__).resolve()
    escape = lambda s: str(s).replace('%', '%%')
    content = ('@echo off\r\nsetlocal DisableDelayedExpansion\r\nchcp 65001 >nul\r\n'
               f'start "" "{escape(python)}" "{escape(viewer)}" "%~dp0{escape(report.name)}"\r\n')
    launcher = report.with_suffix('.cmd')
    temporary = launcher.with_name('.' + launcher.name + '.tmp')
    temporary.write_bytes(content.encode('utf-8'))
    os.replace(temporary, launcher)


def launch_chat(manifest, row, column):
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    command = [str(python), str(Path(__file__).resolve()), '--chat', str(manifest), str(row), str(column)]
    return subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--chat':
        from .report_chat import main as chat_main
        return chat_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args(argv)
    args.report = args.report.resolve()
    for directory in args.report.parents:
        if (directory / '.zemicomp').is_file():
            os.chdir(directory)
            break
    from PyQt5.QtCore import QUrl, QObject, pyqtSlot, QTimer
    from PyQt5.QtWebChannel import QWebChannel
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    from PyQt5.QtGui import QDesktopServices, QIcon
    from PyQt5.QtWidgets import QApplication, QMainWindow, QToolBar, QMessageBox
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineSettings
    app = QApplication([sys.argv[0]])
    icon = QIcon(str(ICON_PATH))
    app.setWindowIcon(icon)
    window = QMainWindow()
    window.setWindowIcon(icon)
    view = QWebEngineView()
    history, position = [], -1
    current = args.report.resolve()

    def open_report(path, add_history=True):
        nonlocal current, position
        path = Path(path).resolve()
        try:
            markup = path.read_text(encoding='utf-8') if path.suffix == '.html' else render_markdown(path)
        except Exception as error:
            QMessageBox.warning(window, 'Cannot open report', str(error))
            return
        current = path
        if add_history:
            del history[position + 1:]
            history.append(path)
            position = len(history) - 1
        window.setWindowTitle('ZEMI · ' + path.name)
        view.setHtml(markup, QUrl.fromLocalFile(str(path)))
        back.setEnabled(position > 0)
        forward.setEnabled(position + 1 < len(history))

    class ReportPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, kind, is_main):
            if url.scheme() == 'zemi-chat':
                try:
                    row, column = map(int, url.toString().split(':')[1:])
                    launch_chat(current.with_suffix('.chat.json'), row, column)
                except Exception as error:
                    QMessageBox.warning(window, 'Cannot open terminal', str(error))
                return False
            if kind == self.NavigationTypeLinkClicked:
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.suffix.lower() in {'.md', '.html'}:
                        QTimer.singleShot(0, lambda: open_report(path))
                    else:
                        QDesktopServices.openUrl(url)
                elif url.scheme() in {'https', 'http'}:
                    QDesktopServices.openUrl(url)
                return False
            return True

    view.setPage(ReportPage(view))
    class Bridge(QObject):
        @pyqtSlot(str)
        def openLink(self, address):
            url = QUrl(address)
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in {'.md', '.html'}:
                    QTimer.singleShot(0, lambda: open_report(path))
                else:
                    QDesktopServices.openUrl(url)
            elif url.scheme() in {'https', 'http'}:
                QDesktopServices.openUrl(url)

        @pyqtSlot(str)
        def openChat(self, address):
            try:
                row, column = map(int, address.split(':')[1:])
                if row < 0 or column < 0:
                    raise ValueError('Invalid cell address')
                launch_chat(current.with_suffix('.chat.json'), row, column)
            except Exception as error:
                QMessageBox.warning(window, 'Cannot open terminal', str(error))
    channel = QWebChannel(view.page())
    bridge = Bridge(channel)
    channel.registerObject('zemi', bridge)
    view.page().setWebChannel(channel)
    from . import env
    cache = env.path.tmp / 'zemi-report-viewer-cache'
    cache.mkdir(parents=True, exist_ok=True)
    view.page().profile().setCachePath(str(cache))
    view.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    toolbar = QToolBar()
    window.addToolBar(toolbar)
    def navigate(delta):
        nonlocal position
        position += delta
        open_report(history[position], False)
    back = toolbar.addAction('Назад', lambda: navigate(-1))
    forward = toolbar.addAction('Вперёд', lambda: navigate(1))
    toolbar.addAction('Обновить', lambda: open_report(current, False))
    window.setCentralWidget(view)
    window.resize(1400, 900)
    open_report(args.report)
    window.show()
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
