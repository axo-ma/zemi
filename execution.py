"""Module-scoped Python kernels; Papermill still executes and saves each run."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_environment = None
_python_path = None


def prepare_run(root):
    """Clear interactive variables and restore the kernel's launch environment."""
    global _environment, _python_path
    if _environment is None:
        _environment, _python_path = dict(os.environ), list(sys.path)
    else:
        os.environ.clear()
        os.environ.update(_environment)
        sys.path[:] = _python_path
    os.chdir(root)
    from IPython import get_ipython
    get_ipython().run_line_magic('reset', '-f')
    from . import playbook
    from .arsenal.libs import _set_client_reuse
    playbook._published = False
    _set_client_reuse(True)


class ModuleKernel:
    def __init__(self, root):
        self.root = Path(root)
        self.manager = None
        self.kernel_name = None

    def acquire(self, kernel_name):
        if self.manager is not None and self.kernel_name != kernel_name:
            self.close()
        if self.manager is None:
            from jupyter_client import KernelManager
            self.manager = KernelManager(kernel_name=kernel_name)
            self.kernel_name = kernel_name
        return self.manager

    def close(self):
        manager, self.manager = self.manager, None
        self.kernel_name = None
        if manager is not None and manager.has_kernel:
            manager.shutdown_kernel(now=True)


def register_engine():
    from papermill.engines import NBClientEngine, papermill_engines
    from papermill.clientwrap import PapermillNotebookClient
    from papermill.log import logger
    import nbformat

    class ModuleKernelEngine(NBClientEngine):
        @classmethod
        def execute_managed_notebook(cls, nb_man, kernel_name, log_output=False,
                                     stdout_file=None, stderr_file=None, start_timeout=60,
                                     execution_timeout=None, zemi_kernel=None, **kwargs):
            manager = zemi_kernel.acquire(kernel_name)
            setup = nbformat.v4.new_code_cell(
                f'import sys\nsys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n'
                'from zemi.execution import prepare_run\n'
                f'prepare_run({str(zemi_kernel.root)!r})', id='zemi-run-setup')
            setup.metadata.update(tags=['zemi-run-setup'], papermill={
                'exception': False, 'start_time': None, 'end_time': None,
                'duration': None, 'status': 'pending'})
            nb_man.nb.cells.insert(0, setup)
            if nb_man.pbar is not None:
                nb_man.pbar.total += 1
            kwargs.pop('input_path', None)
            timeout = kwargs.pop('timeout', None)
            kwargs.pop('startup_timeout', None)
            client = PapermillNotebookClient(nb_man, km=manager, kernel_name=kernel_name,
                log=logger, log_output=log_output, stdout_file=stdout_file,
                stderr_file=stderr_file, timeout=execution_timeout or timeout,
                startup_timeout=start_timeout, **kwargs)
            try:
                return client.execute()
            finally:
                # Each run has its own client channels, while the process survives.
                if client.kc is not None:
                    client.kc.stop_channels()

    papermill_engines.register('zemi-module', ModuleKernelEngine)
