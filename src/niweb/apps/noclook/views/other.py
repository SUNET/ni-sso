# -*- coding: utf-8 -*-
__author__ = 'lundberg'

# -*- coding: utf-8 -*-
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from re import escape as re_escape
import json
from lucenequerybuilder import Q

from apps.noclook.models import NodeHandle, NodeType
from apps.noclook import arborgraph
from apps.noclook import helpers
import norduniclient as nc


def index(request):
    return render_to_response('noclook/index.html', {}, context_instance=RequestContext(request))


@login_required
def logout_page(request):
    """
    Log users out and redirects them to the index.
    """
    logout(request)
    return HttpResponseRedirect('/')


# Visualization views
@login_required
def visualize_json(request, handle_id):
    """
    Creates a JSON representation of the node and the adjacent nodes.
    This JSON data is then used by Arbor.js (http://arborjs.org/) to make
    a visual representation.
    """
    # Get the node
    nh = NodeHandle.objects.get(pk=handle_id)
    root_node = nc.get_node_model(nc.neo4jdb, nh.handle_id)
    if root_node:
        # Create the data JSON structure needed
        graph_dict = arborgraph.create_generic_graph(root_node)
        jsonstr = arborgraph.get_json(graph_dict)
    else:
        jsonstr = '{}'
    return HttpResponse(jsonstr, mimetype='application/json')


@login_required
def visualize(request, slug, handle_id):
    """
    Visualize view with JS that loads JSON data.
    """
    nh = get_object_or_404(NodeHandle, pk=handle_id)
    node = nh.get_node()
    return render_to_response('noclook/visualize/visualize.html', {'node_handle': nh, 'node': node, 'slug': slug},
                              context_instance=RequestContext(request))


@login_required
def visualize_maximize(request, slug, handle_id):
    """
    Visualize view with JS that loads JSON data.
    """
    nh = get_object_or_404(NodeHandle, pk=handle_id)
    node = nh.get_node()
    return render_to_response('noclook/visualize/visualize_maximize.html',
                              {'node_handle': nh, 'node': node, 'slug': slug},
                              context_instance=RequestContext(request))


# Search views
@login_required
def search(request, value='', form=None):
    """
    Search through nodes either from a POSTed search query or through an
    URL like /slug/key/value/ or /slug/value/.
    """
    result = []
    posted = False
    if request.POST:
        value = request.POST.get('q', '')
        posted = True
    if value:
        query = re_escape(value.encode('utf-8'))
        nodes = nc.get_nodes_by_value(nc.neo4jdb, query)
        if form == 'csv':
            return helpers.dicts_to_csv_response(list(nodes))
        elif form == 'xls':
            return helpers.dicts_to_xls_response(list(nodes))
        for node in nodes:
            nh = get_object_or_404(NodeHandle, pk=node['handle_id'])
            item = {'node': node, 'nh': nh}
            result.append(item)
        if len(result) == 1:
            return HttpResponseRedirect(result[0]['nh'].get_absolute_url())
    return render_to_response('noclook/search_result.html', {'value': value, 'result': result, 'posted': posted},
                              context_instance=RequestContext(request))


@login_required
def search_autocomplete(request):
    """
    Search through a pre determined index for *[query]* and returns JSON data
    like below.
    {
     query:'Li',
     suggestions:['Liberia','Liechtenstein','Lithuania'],
     data:['LR','LY','LI','LT']
    }
    """
    response = HttpResponse(mimetype='application/json')
    query = request.GET.get('query', None)
    if query:
        query = re_escape(query)
        q = Q('name', '*%s*' % query, wildcard=True)
        suggestions = []
        for handle_id in nc.legacy_node_index_search(nc.neo4jdb, unicode(q))['result']:
            node = nc.get_node_model(nc.neo4jdb, handle_id)
            suggestions.append(node.data['name'])
        d = {'query': query, 'suggestions': suggestions, 'data': []}
        json.dump(d, response)
        return response
    return False


@login_required
def find_all(request, slug=None, key=None, value=None, form=None):
    """
    Search through nodes either from a POSTed search query or through an
    URL like /slug/key/value/, /slug/value/ /key/value/, /value/ or /key/.
    """
    label = None
    node_type = None
    if request.POST:
        value = request.POST.get('q', '')  # search for '' if blank
    if slug:
        try:
            node_type = get_object_or_404(NodeType, slug=slug)
            label = node_type.get_label()
        except Http404:
            return render_to_response('noclook/search_result.html',
                                      {'node_type': slug, 'key': key, 'value': value, 'result': None,
                                       'node_meta_type': None},
                                      context_instance=RequestContext(request))
    if value:
        nodes = nc.get_nodes_by_value(nc.neo4jdb, value, key, label)
    else:
        nodes = nc.get_nodes_by_type(nc.neo4jdb, label)
    if form == 'csv':
        return helpers.dicts_to_csv_response(list(nodes))
    elif form == 'xls':
        return helpers.dicts_to_xls_response(list(nodes))
    result = []
    for node in nodes:
        nh = get_object_or_404(NodeHandle, pk=node['handle_id'])
        item = {'node': node, 'nh': nh}
        result.append(item)
    return render_to_response('noclook/search_result.html',
                              {'node_type': node_type, 'key': key, 'value': value, 'result': result},
                              context_instance=RequestContext(request))


# Google maps views
@login_required
def gmaps(request, slug):
    return render_to_response('noclook/google_maps.html', {'slug': slug}, context_instance=RequestContext(request))


@login_required
def gmaps_json(request, slug):
    """
    Directs gmap json requests to the right view.
    """
    gmap_views = {
        'sites': gmaps_sites,
        'optical-nodes': gmaps_optical_nodes
    }
    try:
        return gmap_views[slug](request)
    except KeyError:
        raise Http404


@login_required
def gmaps_sites(request):
    """
    Return a json object with node dicts.
    {
        nodes: [
            {
            name: '',
            url: '',
            lng: 0.0,
            lat: 0.0
            },
        ],
        edges: []
    }
    """
    sites = nc.get_nodes_by_type(nc.neo4jdb, 'Site')
    site_list = []
    for site in sites:
        try:
            site = {
                'name': site['name'],
                'url': helpers.get_node_url(site['handle_id']),
                'lng': float(str(site.get('longitude', 0))),
                'lat': float(str(site.get('latitude', 0)))
            }
        except KeyError:
            continue
        site_list.append(site)
    response = HttpResponse(mimetype='application/json')
    json.dump({'nodes': site_list, 'edges': []}, response)
    return response


@login_required
def gmaps_optical_nodes(request):
    """
    Return a json object with dicts of optical node and cables.
    {
    nodes: [
        {
        name: '',
        url: '',
        lng: 0.0,
        lat: 0.0
        },
    ],
    edges: [
        {
        name: '',
        url: '',
        end_points: [{lng: 0.0, lat: 0.0,},]
        }
    ]
    """
    # Cypher query to get all cables with cable type fiber that are connected
    # to two optical node.
    q = """
        MATCH (cable:Cable)
        WHERE cable.cable_type = "Dark Fiber"
        MATCH (cable)-[Connected_to]->port
        WITH cable, port
        MATCH port<-[:Has*0..]-equipment
        WHERE (equipment:Optical_Node) AND NOT equipment.type =~ "(?i).*tss.*"
        WITH cable, port, equipment
        MATCH p2=equipment-[:Located_in]->()<-[:Has*0..]-loc
        WHERE (loc:Site)
        RETURN cable, equipment, loc
        """
    result = nc.query_to_list(nc.neo4jdb, q)
    nodes = {}
    edges = {}
    for item in result:
        node = {
            'name': item['equipment']['name'],
            'url': helpers.get_node_url(item['equipment']['handle_id']),
            'lng': float(str(item['loc'].get('longitude', 0))),
            'lat': float(str(item['loc'].get('latitude', 0)))
        }
        coords = {
            'lng': float(str(item['loc'].get('longitude', 0))),
            'lat': float(str(item['loc'].get('latitude', 0)))
        }
        edge = {
            'name': item['cable']['name'],
            'url': helpers.get_node_url(item['cable']['handle_id']),
            'end_points': [coords]
        }
        nodes[item['equipment']['name']] = node
        if item['cable']['name'] in edges:
            edges[item['cable']['name']]['end_points'].append(coords)
        else:
            edges[item['cable']['name']] = edge
    response = HttpResponse(mimetype='application/json')
    json.dump({'nodes': nodes.values(), 'edges': edges.values()}, response)
    return response


@login_required
def qr_lookup(request, name):
    hits = list(nc.get_nodes_by_name(nc.neo4jdb, name))
    if len(hits) == 1:
        nh = get_object_or_404(NodeHandle, pk=hits[0]['handle_id'])
        return HttpResponseRedirect(nh.get_absolute_url())
    return render_to_response('noclook/qr_result.html', {'hits': hits, 'name': name},
                              context_instance=RequestContext(request))


@login_required
def ip_address_lookup(request):
    if request.POST:
        ip_address = request.POST.get('ip_address', None)
        if ip_address:
            hostname = helpers.get_hostname_from_address(ip_address)
            return HttpResponse(json.dumps(hostname), mimetype='application/json')
    raise Http404


@login_required
def json_table_to_file(request):
    if request.POST:
        file_format = request.POST.get('format', None)
        data = request.POST.get('data', None)
        header = request.POST.get('header', None)
        table = json.loads(data)
        header = json.loads(header)
        if table and file_format == 'csv':
            return helpers.dicts_to_csv_response(table, header)
        elif table and file_format == 'xls':
            return helpers.dicts_to_xls_response(table, header)
    raise Http404