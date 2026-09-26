"""Standard Review Report setup for optimized Modules in a component."""
from zemi.component import ZemiComponent
from zemi.review import configure_review


component = ZemiComponent(params_file="@comp/params/default_params.toml")
try:
    configure_review(component, "@comp/job.exp.py")
    component.run()
finally:
    component.close()
