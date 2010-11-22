from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.http import HttpResponseRedirect
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from niweb.noclook.models import NodeHandle, NodeType
import neo4jclient

def index(request):
    type_list = NodeType.objects.all()
    return render_to_response('noclook/index.html', {},
        context_instance=RequestContext(request))

@login_required
def list_by_type(request, slug):
    type = get_object_or_404(NodeType, slug=slug)
    node_handle_list = type.nodehandle_set.all()
    return render_to_response('noclook/list_by_type.html',
        {'node_handle_list': node_handle_list},
        context_instance=RequestContext(request))

@login_required
def detail(request, handle_id, slug):
    nh = get_object_or_404(NodeHandle, pk=handle_id)
    # Get node from neo4j-database
    nc = neo4jclient.Neo4jClient()
    node = nc.get_node_by_id(nh.node_id)
    return render_to_response('noclook/detail.html',
        {'node_handle': nh, 'node': node},
        context_instance=RequestContext(request))

@login_required
def logout_page(request):
    """
    Log users out and re-direct them to the index.
    """
    logout(request)
    return HttpResponseRedirect('/')
