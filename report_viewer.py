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

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'zemi'

ICON_PATH = Path(__file__).resolve().parent / 'assets' / 'dataset-report.ico'
WINDOWS_APP_ID = 'ZEMI.DatasetReport'

CSS = '''
body{margin:20px;background:#111314;color:#c6c9cc;font:14px/1.45 "Segoe UI",sans-serif}
h1{font-size:23px;color:#eee}h2{font-size:18px}a{color:#40b1d5;text-decoration:none}a:hover,a:focus-visible{text-decoration:underline;text-underline-offset:3px}
.table-wrap{overflow:auto;max-height:72vh;border:1px solid #303538}
table{border-collapse:separate;border-spacing:0;font-size:13px;min-width:100%;width:max-content}
th,td{border-bottom:1px solid #393d40;padding:7px 10px;text-align:left;vertical-align:top;white-space:nowrap}
th{background:#191c1e;position:sticky;top:0;z-index:3;font-weight:600}
table:not(.dataset-items) td:first-child{position:sticky;left:0;background:#111314;z-index:2;min-width:290px;box-shadow:2px 0 0 #393d40}
table:not(.dataset-items) th:first-child{left:0;z-index:4;min-width:290px;box-shadow:2px 0 0 #393d40}
.dataset-items .sticky-item{position:sticky;left:0;box-sizing:border-box;width:290px;min-width:290px;max-width:290px;background:#111314;z-index:2;overflow:hidden;text-overflow:ellipsis}
.dataset-items .sticky-target{position:sticky;left:290px;box-sizing:border-box;max-width:260px;background:#111314;z-index:2;overflow:hidden;text-overflow:ellipsis;box-shadow:2px 0 0 #393d40}
.dataset-items th.sticky-item,.dataset-items th.sticky-target{background:#191c1e;z-index:4}
tbody tr:hover,tbody tr:hover td:first-child,tbody tr:hover td.sticky-target{background:#1b2023}
summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:450px;background:#202529;padding:10px;border-radius:4px;font:12px/1.5 Consolas,monospace}
details[open]{min-width:180px;max-width:450px}code{font-family:Consolas,monospace}
.run-error{color:#ef8181}.run-link{white-space:nowrap}
'''


def render_markdown(path):
    from markdown_it import MarkdownIt
    path = Path(path).resolve()
    markup = MarkdownIt('commonmark', {'html': True}).enable('table').render(path.read_text(encoding='utf-8'))
    # Report HTML supports disclosure tags, but arbitrary scripts must not run
    # with a Python bridge. Remove executable content before adding our script.
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(markup, 'html.parser')
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
    if path.name.endswith('.dataset.md'):
        table = next((t for t in soup.find_all('table')
                      if (lambda names: names and names[0] == 'Item ID' and
                          set(names[1:3]) == {'Matches', 'Target'})(
                              [h.get_text() for h in t.select('thead th')])), None)
        if table:
            headers = table.select('thead th')
            if headers[1].get_text() == 'Matches':
                headers[1].insert_before(headers[2].extract())
                for row in table.select('tbody > tr'):
                    cells = row.find_all('td', recursive=False)
                    if len(cells) >= 3:
                        cells[1].insert_before(cells[2].extract())
            table['class'] = [*table.get('class', []), 'dataset-items']
            headers = table.select('thead th')
            headers[0]['class'] = [*headers[0].get('class', []), 'sticky-item']
            headers[1]['class'] = [*headers[1].get('class', []), 'sticky-target']
            for row_index, row in enumerate(table.select('tbody > tr')):
                tds = row.find_all('td', recursive=False)
                if len(tds) >= 2:
                    tds[0]['class'] = [*tds[0].get('class', []), 'sticky-item']
                    tds[1]['class'] = [*tds[1].get('class', []), 'sticky-target']
                    tds[0]['title'] = tds[0].get_text()
                    tds[1]['title'] = tds[1].get_text()
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding='utf-8'))
                for row_index, row in enumerate(table.select('tbody > tr')):
                    tds = row.find_all('td', recursive=False)
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
                        for child in list(target.contents):
                            anchor.append(child.extract())
                        target.append(anchor)
    for table in soup.find_all('table'):
        wrapper = soup.new_tag('div', attrs={'class': 'table-wrap'})
        table.wrap(wrapper)
    script = '''new QWebChannel(qt.webChannelTransport,function(channel){window.zemiBridge=channel.objects.zemi;});
document.addEventListener('click', function(e){
const a=e.target.closest('a[href]');if(!a||a.getAttribute('href').startsWith('#'))return;
e.preventDefault();e.stopPropagation();if(window.zemiBridge){
if(a.getAttribute('href').startsWith('zemi-chat:'))window.zemiBridge.openChat(a.getAttribute('href'));
else window.zemiBridge.openLink(a.href);}
},true);'''
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; script-src \'unsafe-inline\' qrc:; img-src file:;">'
            f'<base href="{html.escape(path.parent.as_uri() + "/", quote=True)}">'
            f'<style>{CSS}</style></head><body>{soup}<script src="qrc:///qtwebchannel/qwebchannel.js"></script><script>{script}</script></body></html>')


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
            markup = render_markdown(path)
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
                    if path.suffix.lower() == '.md':
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
                if path.suffix.lower() == '.md':
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
