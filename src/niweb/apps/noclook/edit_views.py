# -*- coding: utf-8 -*-
"""
Created on Wed Dec 14 14:00:03 2011

@author: lundberg

Node manipulation views.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.template.defaultfilters import slugify
from niweb.apps.noclook.models import NodeHandle, NodeType
from niweb.apps.noclook import forms
from niweb.apps.noclook import helpers as h

import norduni_client as nc
import ipaddr
import json
from lucenequerybuilder import Q

# We should move this kind of data to the SQL database.
COUNTRY_MAP = {
    'DE': 'Germany',    
    'DK': 'Denmark',
    'FI': 'Finland',
    'IS': 'Iceland',
    'NL': 'Netherlands',
    'NO': 'Norway',
    'SE': 'Sweden',    
    'UK': 'United Kingdom',
    'US': 'USA'
}

# Helper functions
def get_nh_node(node_handle_id):
    '''
    Takes a node handle id and returns the node handle and the node.
    '''
    nh = get_object_or_404(NodeHandle, pk=node_handle_id)
    node = nh.get_node()
    return nh, node
    
def slug_to_node_type(slug):
    '''
    Returns or creates and returns the NodeType object from the supplied slug.
    '''
    acronym_types = ['odf']
    node_type, created = NodeType.objects.get_or_create(slug=slug)
    if created:
        if slug in acronym_types:
            type_name = slug.upper()
        else:
            type_name = slug.replace('-', ' ').title()
        node_type.type = type_name
        node_type.save()
    return node_type

def form_update_node(user, node, form, property_keys=[]):
    '''
    Take a node, a form and the property keys that should be used to fill the
    node if the property keys are omitted the form.base_fields will be used.
    Returns True if all non-empty properties where added else False and 
    rollbacks the node changes.
    '''
    meta_fields = ['relationship_location']
    nh = get_object_or_404(NodeHandle, pk=node['handle_id'])
    if not property_keys:
        for field in form.base_fields.keys():
            if field not in meta_fields:
                property_keys.append(field)
    for key in property_keys:
        try:
            if form.cleaned_data[key] or form.cleaned_data[key] == 0:
                pre_value = node.getProperty(key, '')
                if pre_value != form.cleaned_data[key]:
                    with nc.neo4jdb.transaction:
                        node[key] = form.cleaned_data[key]
                    if key == 'name':
                        nh.node_name = form.cleaned_data[key]
                    nh.modifier = user
                    nh.save()
            elif not form.cleaned_data[key] and key != 'name':
                with nc.neo4jdb.transaction:
                    del node[key] 
        except KeyError:
            return False
        except RuntimeError:
            # If the property type differs from what is allowed in node 
            # properties. Force string as last alternative.
            with nc.neo4jdb.transaction:
                node[key] = unicode(form.cleaned_data[key])
    return True

def place_physical_in_location(nh, node, location_id):
    '''
    Places a physical node in a rack or on a site. Also converts it to a 
    physical node if it still is a logical one.
    ''' 
    # Check if the node is logical
    meta_type = nc.get_node_meta_type(node)
    if meta_type == 'logical':
        # Make the node physical
        with nc.neo4jdb.transaction:        
            nc.delete_relationship(nc.neo4jdb,
                                   h.iter2list(node.Contains.incoming)[0])
            physical = nc.get_meta_node(nc.neo4jdb, 'physical')
            nc.create_relationship(nc.neo4jdb, physical, node, 'Contains')
            nh.node_meta_type = 'physical'
            nh.save()
    location_node = nc.get_node_by_id(nc.neo4jdb,  location_id)
    rel_exist = nc.get_relationships(node, location_node, 'Located_in')
    # If the location is the same as before just update relationship
    # properties
    if rel_exist:
        # TODO: Change properties here
        #location_rel = rel_exist[0]
        #with nc.neo4jdb.transaction:
        pass
    else:
        # Remove the old location(s) and create a new
        for rel in h.iter2list(node.Located_in.outgoing):
            nc.delete_relationship(nc.neo4jdb, rel)
        nc.create_suitable_relationship(nc.neo4jdb, node, 
                                        location_node, 'Located_in')
    return nh, node

def place_location_in_location(node, location_id):
    '''
    Places a location node in another location.
    '''
    location_node = nc.get_node_by_id(nc.neo4jdb,  location_id)
    rel_exist = nc.get_relationships(location_node, node, 'Has')
    # If the location is the same as before just update relationship
    # properties
    if rel_exist:
        # TODO: Change properties here
        #location_rel = rel_exist[0]
        #with nc.neo4jdb.transaction:
        pass
    else:
        # Remove the old location(s) and create a new
        for rel in h.iter2list(node.Has.incoming):
            nc.delete_relationship(nc.neo4jdb, rel)
            nc.create_suitable_relationship(nc.neo4jdb, location_node,
                                            node, 'Has')
    return node
    
def connect_physical(node, other_node_id):
    '''
    Places a location node in another location.
    '''
    other_node = nc.get_node_by_id(nc.neo4jdb,  other_node_id)
    rel_exist = nc.get_relationships(node, other_node, 'Connected_to')
    # If the location is the same as before just update relationship
    # properties
    if rel_exist:
        # TODO: Change properties here
        #location_rel = rel_exist[0]
        #with nc.neo4jdb.transaction:
        pass
    else:
        nc.create_suitable_relationship(nc.neo4jdb, node, other_node,
                                        'Connected_to')
    return node

@login_required
def delete_node(request, slug, handle_id):
    '''
    Removes the node and all relationships to and from that node.
    '''
    nh, node = get_nh_node(handle_id)
    if nc.get_node_meta_type(node) == 'physical':
        for rel in node.Has.outgoing:
            child_nh, child_node = get_nh_node(rel.end['handle_id'])
            child_nh.delete()
    nh.delete()
    return HttpResponseRedirect('/%s' % slug)
    
@login_required
def delete_relationship(request, slug, handle_id, rel_id):
    '''
    Removes the relationship if the node has a relationship matching the
    supplied id.
    '''
    nh, node = get_nh_node(handle_id)
    rel = nc.get_relationship_by_id(nc.neo4jdb, rel_id)
    if rel.start.id == node.id or rel.end.id == node.id:
        if nc.delete_relationship(nc.neo4jdb, rel):
            return HttpResponseRedirect('/%s/%d/edit' % (slug, nh.handle_id))
    raise Http404
    
# Form data returns
@login_required
def get_node_type(request, slug):
    '''
    Compiles a list of alla nodes of that node type and returns a list of
    node name, node id tuples.
    '''
    node_type = slug_to_node_type(slug)
    q = '''                   
        START node=node:node_types(node_type="%s")
        RETURN node
        ORDER BY node.country_code?, node.name
        ''' % node_type
    hits = nc.neo4jdb.query(q)
    type_list = []
    for hit in hits:
        if hit['node']['node_type'] == 'Site':
            name = '%s-%s' % (hit['node']['country_code'], hit['node']['name'])
        else:
            name = hit['node']['name']
        type_list.append((hit['node'].id, name))
    return HttpResponse(json.dumps(type_list), mimetype='application/json')

@login_required
def get_children(request, node_id, slug=None):
    '''
    Compiles a list of the nodes children and returns a list of
    node name, node id tuples. If node_type is set the function will only return
    nodes of that type.
    '''
    type_filter = ''
    if slug:
        type_filter = 'and child.node_type = "%s"' % slug_to_node_type(slug)
    q = '''                   
        START parent=node(%d)
        MATCH parent--child
        WHERE (parent-[:Has]->child or parent<-[:Located_in]-child) %s
        RETURN child
        ORDER BY child.node_type, child.name
        ''' % (int(node_id), type_filter)
    hits = nc.neo4jdb.query(q)
    child_list = []
    try:
        for hit in hits:
            name = '%s %s' % (hit['child']['node_type'], hit['child']['name'])
            child_list.append((hit['child'].id, name))
    except AttributeError:
        pass
    return HttpResponse(json.dumps(child_list), mimetype='application/json')

# Create functions
@login_required
def new_node(request, slug=None):
    '''
    Generic create function that creates a generic node and redirects calls to 
    node type sensitive create functions.
    '''
    if not request.user.is_staff:
        raise Http404
    # Template name is create_type_slug.html.
    template = 'noclook/edit/create_%s.html' % slug
    template = template.replace('-', '_')
    if request.POST:
        form = NEW_FORMS[slug](request.POST)
        if form.is_valid():
            node_name = form.cleaned_data['name']
            node_type = slug_to_node_type(slug)
            node_meta_type = request.POST['meta_type']
            node_handle = NodeHandle(node_name=node_name,
                                node_type=node_type,
                                node_meta_type=node_meta_type,
                                modifier=request.user, creator=request.user)
            node_handle.save()
            h.set_noclook_auto_manage(nc.neo4jdb, node_handle.get_node(),
                                       False)
            try:
                func = NEW_FUNC[node_type.slug]
            except KeyError:
                raise Http404
            return func(request, node_handle.handle_id, form)
        else:
            return render_to_response(template, {'form': form},
                                context_instance=RequestContext(request))
    if not slug:
        return render_to_response('noclook/edit/new_node.html', {})
    else:
        try:
            form = NEW_FORMS[slug]
        except KeyError:
            raise Http404
        return render_to_response(template, {'form': form},
                                context_instance=RequestContext(request))
                                
@login_required
def new_site(request, handle_id, form):
    nh, node = get_nh_node(handle_id)
    keys = ['country_code', 'address', 'postarea', 'postcode']
    form_update_node(request.user, node, form, keys)
    with nc.neo4jdb.transaction:
        node['name'] = form.cleaned_data['name'].upper()
        node['country'] = COUNTRY_MAP[node['country_code']]
    return HttpResponseRedirect(nh.get_absolute_url())
    
@login_required
def new_site_owner(request, handle_id, form):
    nh, node = get_nh_node(handle_id)
    keys = ['url']
    form_update_node(request.user, node, form, keys)
    return HttpResponseRedirect(nh.get_absolute_url())
    
@login_required
def new_cable(request, handle_id, form):
    nh, node = get_nh_node(handle_id)
    keys = ['cable_type']
    form_update_node(request.user, node, form, keys)
    return HttpResponseRedirect(nh.get_absolute_url())

@login_required
def new_rack(request, handle_id, form):
    nh, node = get_nh_node(handle_id)
    form_update_node(request.user, node, form)
    if form.cleaned_data['relationship_location']:
        location_id = form.cleaned_data['relationship_location']
        location_node = nc.get_node_by_id(nc.neo4jdb,  location_id)
        rel_exist = nc.get_relationships(location_node, node, 'Has')
        if not rel_exist:
            try:
                location_rel = h.iter2list(node.Has.incoming)
                with nc.neo4jdb.transaction:
                    location_rel[0].delete()
            except IndexError:
                # No site set
                pass
            nc.create_relationship(nc.neo4jdb, location_node, node, 'Has')
    return HttpResponseRedirect(nh.get_absolute_url())

@login_required        
def new_odf(request, handle_id, form):
    nh, node = get_nh_node(handle_id)
    form_update_node(request.user, node, form)
    if form.cleaned_data['number_of_ports']:
        number_of_ports = int(form.cleaned_data['number_of_ports'])
        node_type = slug_to_node_type('port')
        node_meta_type='physical'
        for port in range(1, number_of_ports+1):
            node_handle = NodeHandle(node_name=port, node_type=node_type,
                                    node_meta_type=node_meta_type,
                                    modifier=request.user, creator=request.user)
            node_handle.save()
            port_node = node_handle.get_node()
            h.set_noclook_auto_manage(nc.neo4jdb, port_node, False)
            nc.create_relationship(nc.neo4jdb, node, port_node, 'Has')
            
    return HttpResponseRedirect(nh.get_absolute_url())

# Edit functions
@login_required
def edit_node(request, slug, handle_id):
    '''
    Generic edit function that redirects calls to node type sensitive edit 
    functions.
    '''
    if not request.user.is_staff:
        raise Http404
    try:
        func = EDIT_FUNC[slug]
    except KeyError:
        raise Http404
    return func(request, handle_id)

@login_required
def edit_site(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    site_owner = h.iter2list(node.Responsible_for.incoming)
    if request.POST:
        form = forms.EditSiteForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Site specific updates
            with nc.neo4jdb.transaction:
                node['name'] = form.cleaned_data['name'].upper()
                node['country'] = COUNTRY_MAP[node['country_code']]
            if form.cleaned_data['relationship_site_owner']:
                owner_id = form.cleaned_data['relationship_site_owner']
                owner_node = nc.get_node_by_id(nc.neo4jdb, owner_id)
                rel_exist = nc.get_relationships(node, owner_node, 
                                                     'Responsible_for')
                if not rel_exist:
                    try:
                        owner_rel = h.iter2list(node.Responsible_for.incoming)
                        with nc.neo4jdb.transaction:
                            owner_rel[0].delete()
                    except IndexError:
                        # No site owner set
                        pass
                    nc.create_suitable_relationship(nc.neo4jdb, owner_node,
                                                    node, 'Responsible_for')
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_site.html',
                                  {'node': node, 'form': form,
                                   'site_owner': site_owner},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditSiteForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_site.html',
                                  {'form': form, 'site_owner': site_owner,
                                   'node': node},
                                context_instance=RequestContext(request))

@login_required
def edit_site_owner(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    if request.POST:
        form = forms.EditSiteOwnerForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_site_owner.html',
                                  {'node': node, 'form': form},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditSiteOwnerForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_site_owner.html',
                                  {'form': form, 'node': node},
                                context_instance=RequestContext(request))

@login_required
def edit_cable(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    connections = h.get_connected_cables(node)
    if request.POST:
        form = forms.EditCableForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Cable specific update
            if form.cleaned_data['telenor_trunk_id']:
                with nc.neo4jdb.transaction:
                    node['name'] = form.cleaned_data['telenor_trunk_id']
                    nh.node_name = form.cleaned_data['telenor_trunk_id']
                    nh.save()
            if form.cleaned_data['relationship_end_a']:
                end_a = form.cleaned_data['relationship_end_a']
                connect_physical(node, end_a)
            if form.cleaned_data['relationship_end_b']:
                end_b = form.cleaned_data['relationship_end_b']
                connect_physical(node, end_b)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_cable.html',
                                  {'node': node, 'form': form,
                                   'connections': connections},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditCableForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_cable.html',
                                  {'form': form, 'node': node,
                                   'connections': connections},
                                context_instance=RequestContext(request))
                                
@login_required
def edit_optical_node(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    location = h.get_location(node)
    if request.POST:
        form = forms.EditOpticalNodeForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Optical Node specific updates
            if form.cleaned_data['relationship_location']:
                location_id = form.cleaned_data['relationship_location']
                nh, node = place_physical_in_location(nh, node, location_id)
            else:
                # Remove existing location if any
                for rel in h.iter2list(node.Located_in.outgoing):
                    nc.delete_relationship(nc.neo4jdb, rel)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_optical_node.html',
                                  {'node': node, 'form': form,
                                   'location': location},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditOpticalNodeForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_optical_node.html',
                                  {'form': form, 'location': location,
                                   'node': node},
                                context_instance=RequestContext(request))
@login_required        
def edit_peering_partner(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    if request.POST:
        form = forms.EditPeeringPartnerForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_peering_partner.html',
                                  {'node': node, 'form': form},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditPeeringPartnerForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_peering_partner.html',
                                  {'node': node, 'form': form},
                                context_instance=RequestContext(request))

@login_required        
def edit_rack(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    location = h.get_place(node)
    if request.POST:
        form = forms.EditRackForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Rack specific updates
            if form.cleaned_data['relationship_location']:
                location_id = form.cleaned_data['relationship_location']
                node = place_location_in_location(node, location_id)
            else:
                # Remove existing location if any
                for rel in h.iter2list(node.Has.incoming):
                    nc.delete_relationship(nc.neo4jdb, rel)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_rack.html',
                                  {'node': node, 'form': form,
                                   'location': location},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditRackForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_rack.html',
                                  {'form': form, 'location': location,
                                   'node': node},
                                context_instance=RequestContext(request))

@login_required        
def edit_host(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    location = h.get_location(node)
    if request.POST:
        form = forms.EditHostForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Host specific updates
            if form.cleaned_data['relationship_location']:
                location_id = form.cleaned_data['relationship_location']
                nh, node = place_physical_in_location(nh, node, location_id) 
            else:
                # Remove existing location if any
                for rel in h.iter2list(node.Located_in.outgoing):
                    nc.delete_relationship(nc.neo4jdb, rel)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_host.html',
                                  {'node': node, 'form': form,
                                   'location': location},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditHostForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_host.html',
                                  {'form': form, 'location': location,
                                   'node': node},
                                context_instance=RequestContext(request))

@login_required        
def edit_router(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    location = h.get_location(node)
    if request.POST:
        form = forms.EditRouterForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Router specific updates
            if form.cleaned_data['relationship_location']:
                location_id = form.cleaned_data['relationship_location']
                nh, node = place_physical_in_location(nh, node, location_id) 
            else:
                # Remove existing location if any
                for rel in h.iter2list(node.Located_in.outgoing):
                    nc.delete_relationship(nc.neo4jdb, rel)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_router.html',
                                  {'node': node, 'form': form, 
                                   'location': location},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditRouterForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_router.html',
                                  {'node': node, 'form': form,
                                   'location': location},
                                context_instance=RequestContext(request)) 

@login_required
def edit_odf(request, handle_id):
    if not request.user.is_staff:
        raise Http404
    # Get needed data from node
    nh, node = get_nh_node(handle_id)
    location = h.get_location(node)
    if request.POST:
        form = forms.EditOdfForm(request.POST)
        if form.is_valid():
            # Generic node update
            form_update_node(request.user, node, form)
            # Host specific updates
            if form.cleaned_data['relationship_location']:
                location_id = form.cleaned_data['relationship_location']
                nh, node = place_physical_in_location(nh, node, location_id) 
            else:
                # Remove existing location if any
                for rel in h.iter2list(node.Located_in.outgoing):
                    nc.delete_relationship(nc.neo4jdb, rel)
            return HttpResponseRedirect(nh.get_absolute_url())
        else:
            return render_to_response('noclook/edit/edit_odf.html',
                                  {'node': node, 'form': form,
                                   'location': location},
                                context_instance=RequestContext(request))
    else:
        form = forms.EditOdfForm(h.item2dict(node))
        return render_to_response('noclook/edit/edit_odf.html',
                                  {'form': form, 'location': location,
                                   'node': node},
                                context_instance=RequestContext(request))

NEW_FORMS =  {'cable': forms.NewCableForm,
              'odf': forms.NewOdfForm,
              'rack': forms.NewRackForm,
              'site': forms.NewSiteForm, 
              'site-owner': forms.NewSiteOwnerForm,
             }
              
#EDIT_FORMS =  {'cable': forms.EditCableForm,
#               'host': forms.EditHostForm,
#               'optical-node': forms.EditOpticalNodeForm,
#               'router': forms.EditRouterForm,
#               'site': forms.EditSiteForm,
#               'site-owner': forms.EditSiteOwnerForm,
#               }

NEW_FUNC = {'cable': new_cable,
            'odf': new_odf,
            'rack': new_rack,
            'site': new_site,
            'site-owner': new_site_owner,
            }

EDIT_FUNC = {'cable': edit_cable,
             'host': edit_host,
             'odf': edit_odf,
             'optical-node': edit_optical_node,
             'peering-partner': edit_peering_partner,
             'rack': edit_rack,
             'router': edit_router,
             'site': edit_site, 
             'site-owner': edit_site_owner,
             }

#@login_required
#def new_node_old(request):
#    if not request.user.is_staff:
#        raise Http404    
#    if request.POST:
#        # Create the new node
#        node_name = request.POST['name']
#        node_type = get_object_or_404(NodeType, slug=request.POST['node_types'])
#        node_meta_type = request.POST['meta_types'].lower()
#        node_handle = NodeHandle(node_name=node_name,
#                                node_type=node_type,
#                                node_meta_type=node_meta_type,
#                                modifier=request.user, creator=request.user)
#        node_handle.save()
#        nc.set_noclook_auto_manage(nc.neo4jdb, node_handle.get_node(), False)
#        return edit_node(request, slugify(node_handle.node_type), 
#                                                         node_handle.handle_id)
#    else:
#        node_types = get_list_or_404(NodeType)
#
#    return render_to_response('noclook/new_node2.html',
#                            {'node_types': node_types},
#                            context_instance=RequestContext(request))
#
#@login_required
#def edit_node_old(request, slug, handle_id, node=None, message=None):
#    '''
#    View used to change and add properties to a node, also to delete
#    a node relationships.
#    '''
#    if not request.user.is_staff:
#        raise Http404
#    nh = get_object_or_404(NodeHandle, pk=handle_id)
#    if not node:
#        node = nh.get_node()
#    # Make a dict of properties you want to be able to change
#    node_properties = {}
#    unwanted_properties = ['handle_id', 'node_type', 'noclook_auto_manage',
#                           'noclook_last_seen']
#    for key, value in node.items():
#        if key not in unwanted_properties:
#            try:
#                node_properties[key] = json.dumps(value)
#            except ValueError:
#                node_properties[key] = value
#    # Relationships
#    # Make a dict of relationships you want to be able to change
#    unwanted_relationships = ['Contains', 'Consist_of']
#    node_relationships = []
#    for rel in node.relationships:
#        relationship = {'properties': {}}
#        if rel.type.name() not in unwanted_relationships:
#            relationship['id'] = rel.id
#            relationship['start'] = rel.start['name']
#            relationship['type'] = rel.type.name()
#            relationship['end'] = rel.end['name']
#            for key in rel.getPropertyKeys():
#                if key not in unwanted_properties:
#                    relationship[key] = rel[key]
#            node_relationships.append(relationship)
#    return render_to_response('noclook/edit_node.html',
#                            {'node_handle': nh, 'node': node,
#                            'node_properties': node_properties,
#                            'node_relationships': node_relationships,
#                            'message': message},
#                            context_instance=RequestContext(request))
#
#@login_required
#def save_node_old(request, slug, handle_id):
#    '''
#    Updates the node and node_handle with new values.
#    '''
#    if not request.user.is_staff:
#        raise Http404
#    nh = get_object_or_404(NodeHandle, pk=handle_id)
#    node = nh.get_node()
#    if request.POST:
#        # request.POST is immutable.
#        post = request.POST.copy()
#        new_properties = {}
#        del post['csrfmiddlewaretoken']
#        # Add all new properties
#        for i in range(0, len(post)):
#            # To make this work we need js in the template to add new
#            # input with name new_keyN and new_valueN.
#            nk = 'new_key%d' % i
#            nv = 'new_value%d' % i
#            if (nk in post) and (nv in post):
#                #QueryDicts uses lists a values
#                new_properties[post[nk]] = post.get(nv)
#                del post[nk]
#                del post[nv]
#        # Add the remaining properties
#        for item in post:
#            new_properties[item] = post.get(item)
#        # Update the node
#        node = nc.update_item_properties(nc.neo4jdb, node, new_properties)
#        # Update the node_handle
#        nh.node_name = node['name']
#        nh.modifier = request.user
#        nc.update_noclook_auto_manage(nc.neo4jdb, node)
#        nh.save()
#    return edit_node(request, slug, handle_id, node=node)
#    
#@login_required
#def delete_node_old(request, slug, handle_id):
#    '''
#    Deletes the NodeHandle from the SQL database and the node from the Neo4j
#    database.
#    '''    
#    if not request.user.is_staff:
#        raise Http404
#    if request.POST:
#        if 'confirmed' in request.POST and \
#                                        request.POST['confirmed'] == 'delete':
#            nh = get_object_or_404(NodeHandle, pk=handle_id)
#            nh.delete()
#            return HttpResponseRedirect('/%s/' % slug) 
#    return edit_node(request, slug, handle_id)
#
#@login_required
#def new_relationship_old(request, slug, handle_id):
#    '''
#    Create a new relationship between the node that was edited and another node.
#    
#    The way to get the nodes that are suitible for relationships have to be
#    tought over again. This way is pretty hary.
#    '''
#    if not request.user.is_staff:
#        raise Http404
#    nh = get_object_or_404(NodeHandle, pk=handle_id)
#    node = nh.get_node()
#    message = ''
#    if request.POST:
#        if request.POST['direction']:
#            direction = request.POST['direction']
#            node_id = request.POST['nodes']
#            other_node = nc.get_node_by_id(nc.neo4jdb, node_id)
#            rel_type = request.POST['types']
#            if direction == 'out':
#                rel = nc.create_suitable_relationship(nc.neo4jdb, node,
#                                                      other_node, rel_type)
#            else:
#                rel = nc.create_suitable_relationship(nc.neo4jdb, other_node,
#                                                      node, rel_type)
#            if rel:
#                nc.set_noclook_auto_manage(nc.neo4jdb, rel, False)
#                return edit_relationship(request, slug, handle_id, rel.id, rel)
#            else:
#                message = 'The requested relationship could not be made.' 
#        else:
#            message = 'You have to choose relationship direction.'
#    node_dicts = []
#    suitable_nodes = nc.get_suitable_nodes(nc.neo4jdb, node)
#    for item in ['physical', 'logical', 'relation', 'location']:
#        for n in suitable_nodes[item]:
#            parent = nc.get_root_parent(nc.neo4jdb, n)
#            if parent:
#                name = '%s %s' % (parent['name'], n['name'])      
#            else:
#                name = n['name']
#                node_type = n['node_type']
#            node_dicts.append({'name': name, 
#                               'id':n.id, 
#                               'node_type': node_type})
#    return render_to_response('noclook/new_relationship.html',
#                            {'node_handle': nh, 'node': node, 
#                             'node_dicts': node_dicts, 'message': message},
#                            context_instance=RequestContext(request))
#
#@login_required
#def edit_relationship_old(request, slug, handle_id, rel_id, rel=None, message=None):
#    '''
#    View to update, change or delete relationships properties.
#    '''
#    if not request.user.is_staff:
#        raise Http404
#    nh = get_object_or_404(NodeHandle, pk=handle_id)
#    if not rel:
#        rel = nc.get_relationship_by_id(nc.neo4jdb, rel_id)
#    rel_properties = {}
#    for key in rel.getPropertyKeys():
#        rel_properties[key] = rel[key]
#    return render_to_response('noclook/edit_relationship.html',
#                            {'node_handle': nh, 'rel': rel, 
#                             'rel_properties': rel_properties, 
#                             'message': message},
#                            context_instance=RequestContext(request))
#
#@login_required
#def save_relationship_old(request, slug, handle_id, rel_id):
#    if not request.user.is_staff:
#        raise Http404
#    rel = nc.get_relationship_by_id(nc.neo4jdb, rel_id)
#    if request.POST:
#        # request.POST is immutable.
#        post = request.POST.copy()
#        new_properties = {}
#        del post['csrfmiddlewaretoken']
#        # Add all new properties
#        for i in range(0, len(post)):
#            # To make this work we need js in the template to add new
#            # input with name new_keyN and new_valueN.
#            nk = 'new_key%d' % i
#            nv = 'new_value%d' % i
#            if (nk in post) and (nv in post):
#                #QueryDicts uses lists a values
#                new_properties[post[nk]] = post.get(nv)
#                del post[nk]
#                del post[nv]
#        # Add the remaining properties
#        for item in post:
#            new_properties[item] = post.get(item)
#        # Update the relationships properties
#        rel = nc.update_item_properties(nc.neo4jdb, rel, new_properties)
#        nc.update_noclook_auto_manage(nc.neo4jdb, rel)
#    return edit_relationship(request, slug, handle_id, rel_id, rel)
#
#@login_required
#def delete_relationship_old(request, slug, handle_id, rel_id):
#    '''
#    Deletes the relationship if POST['confirmed']==True.
#    '''
#    if not request.user.is_staff or not request.POST:
#        raise Http404
#    if 'confirmed' in request.POST.keys():
#        nh = get_object_or_404(NodeHandle, pk=handle_id)
#        node = nh.get_node()
#        message = 'No relationship matching the query was found. Nothing deleted.'
#        for rel in node.relationships:
#            cur_id = str(rel.id)
#            if cur_id == rel_id and cur_id in request.POST['confirmed']:
#                message = 'Relationship %s %s %s deleted.' % (rel.start['name'],
#                                                              rel.type,
#                                                              rel.end['name']) 
#                with nc.neo4jdb.transaction:
#                    rel.delete()
#                break                
#        return edit_node(request, slug, handle_id, message=message)
#    else:            
#        message = 'Please confirm the deletion of the relationship.'
#        return edit_node(request, slug, handle_id, message=message)
