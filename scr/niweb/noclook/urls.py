# This also imports the include function
from django.conf.urls.defaults import *

urlpatterns = patterns('niweb.noclook.views',
    (r'^$', 'index'),
    (r'^(?P<handle_id>\d+)/$', 'detail'),
)