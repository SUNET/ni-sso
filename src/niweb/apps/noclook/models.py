# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User
from django_comments.signals import comment_was_posted, comment_was_flagged
from django.dispatch import receiver
from django_comments.models import Comment
from django.urls import reverse
from actstream import action
try:
    from neo4j.exceptions import CypherError
except ImportError:
    try:
        # pre neo4j 1.4
        from neo4j.v1.exceptions import CypherError
    except ImportError:
        # neo4j 1.1
        from neo4j.v1.api import CypherError


import norduniclient as nc
import re
import logging

logger = logging.getLogger('noclook.models')


NODE_META_TYPE_CHOICES = zip(nc.META_TYPES, nc.META_TYPES)


class NodeType(models.Model):
    type = models.CharField(unique=True, max_length=255)
    slug = models.SlugField(unique=True, help_text='Automatically generated from type. Must be unique.')
    hidden = models.BooleanField(default=False, help_text="Hide from menus")

    def __str__(self):
        return self.type

    def get_slug(self):
        return self.slug

    def get_label(self):
        return self.type.replace(' ', '_')

    def get_absolute_url(self):
        return self.url()

    def url(self):
        return reverse('generic_list', args=[self.slug])

    def delete(self, **kwargs):
        """
        Delete the NodeType object with all associated NodeHandles.
        """
        for nh in NodeHandle.objects.filter(node_type=self.pk):
            nh.delete()
        super(NodeType, self).delete()
        return True
    delete.alters_data = True


class NodeHandle(models.Model):
    # Handle <-> Node data
    handle_id = models.AutoField(primary_key=True)
    # Data shared with the node
    node_name = models.CharField(max_length=200)
    node_type = models.ForeignKey(NodeType, on_delete=models.CASCADE)
    node_meta_type = models.CharField(max_length=255, choices=NODE_META_TYPE_CHOICES)
    # Meta information
    creator = models.ForeignKey(User, related_name='creator', null=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    modifier = models.ForeignKey(User, related_name='modifier', null=True, on_delete=models.SET_NULL)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return '%s %s' % (self.node_type, self.node_name)

    def get_node(self):
        """
        Returns the NodeHandles node.
        """
        return nc.get_node_model(nc.graphdb.manager, self.handle_id)

    def get_absolute_url(self):
        return self.url()

    def url(self):
        return reverse('generic_detail', args=[self.node_type.get_slug(), self.handle_id])

    def _create_node(self, label=None):
        if not label:
            label = self.node_type.get_label()
        try:
            nc.create_node(nc.graphdb.manager, self.node_name, self.node_meta_type, label, self.handle_id)
        except CypherError:
            #  A node associated with this handle_id already exists
            pass

    def save(self, *args, **kwargs):
        """
        Create a new node and associate it to the handle.
        """
        super(NodeHandle, self).save(*args, **kwargs)
        self._create_node()
        return self

    save.alters_data = True

    def delete(self, **kwargs):
        """
        Delete that node handle and the handles node.
        """
        try:
            self.get_node().delete()
        except nc.exceptions.NodeNotFound:
            pass
        Comment.objects.filter(object_pk=self.pk).delete()
        super(NodeHandle, self).delete()

    delete.alters_data = True


class UniqueIdGenerator(models.Model):
    """
    Model that provides a base id counter, prefix, suffix and id length. When a new
    id is generated the base id counter will increase by 1.
    """
    name = models.CharField(max_length=256, unique=True)
    base_id = models.IntegerField(default=1)
    zfill = models.BooleanField()
    base_id_length = models.IntegerField(default=0,
                                         help_text="Base id will be filled with leading zeros to this length if zfill is checked.")
    prefix = models.CharField(max_length=256, null=True, blank=True)
    suffix = models.CharField(max_length=256, null=True, blank=True)
    last_id = models.CharField(max_length=256, editable=False)
    next_id = models.CharField(max_length=256, editable=False)
    # Meta
    creator = models.ForeignKey(User, related_name='unique_id_creator', null=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    modifier = models.ForeignKey(User, null=True, blank=True, related_name='unique_id_modifier', on_delete=models.SET_NULL)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def get_id(self):
        """
        Returns the next id and increments the base_id field.
        """
        base_id = self.base_id
        if self.zfill:
            base_id = str(self.base_id).zfill(self.base_id_length)
        prefix = suffix = ''
        if self.prefix:
            prefix = self.prefix
        if self.suffix:
            suffix = self.suffix
        unique_id = '%s%s%s' % (prefix, base_id, suffix)
        self.last_id = unique_id
        self.base_id += 1
        self.save()
        return unique_id

    def get_regex(self):
        prefix = suffix = ''
        if self.prefix:
            prefix = self.prefix
        if self.suffix:
            suffix = self.suffix
        # TODO: handle zerofill?
        return re.compile('({}\d+{})'.format(prefix, suffix))

    def save(self, *args, **kwargs):
        """
        Increments the base_id.
        """
        base_id = self.base_id
        if self.zfill:
            base_id = str(self.base_id).zfill(self.base_id_length)
        prefix = suffix = ''
        if self.prefix:
            prefix = self.prefix
        if self.suffix:
            suffix = self.suffix
        self.next_id = '%s%s%s' % (prefix, base_id, suffix)
        super(UniqueIdGenerator, self).save(*args, **kwargs)

    save.alters_data = True


class UniqueId(models.Model):
    """
    Table for reserving ids and to help ensuring uniqueness across the
    different node types.
    """
    unique_id = models.CharField(max_length=256, unique=True)
    reserved = models.BooleanField(default=False)
    reserve_message = models.CharField(max_length=512, null=True, blank=True)
    reserver = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    site = models.ForeignKey(NodeHandle, null=True, blank=True, on_delete=models.SET_NULL)
    # Meta
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.unique_id


class NordunetUniqueId(UniqueId):
    """
    Collection of all NORDUnet IDs to ensure uniqueness.
    """

    def __str__(self):
        return 'NORDUnet: %s' % self.unique_id


# Can be deleted, just here to not mess up migrations
class OpticalNodeType(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class ServiceClass(models.Model):
    name = models.CharField(unique=True, max_length=255)

    def __str__(self):
        return "{}".format(self.name)


class ServiceType(models.Model):
    name = models.CharField(unique=True, max_length=255)
    service_class = models.ForeignKey(ServiceClass, on_delete=models.CASCADE)

    def as_choice(self):
        return self.name, u'{} - {}'.format(self.service_class.name, self.name)

    def __str__(self):
        return "{}".format(self.name)


class DummyDropdown(object):
    def __init__(self, name):
        self.name = name

    def as_choices(self, empty=True):
        if empty:
            return [('', '')]
        return []

    def as_values(self, empty=True):
        if empty:
            return ['']
        return []


class Dropdown(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def as_choices(self, empty=True):
        choices = [choice.as_choice() for choice in self.choice_set.order_by('name')]
        if empty:
            choices = [('', '')] + choices
        return choices

    def as_values(self, empty=True):
        values = [choice.value for choice in self.choice_set.order_by('name')]
        if empty:
            values = [''] + values
        return values

    def __str__(self):
        return "{}".format(self.name)

    @staticmethod
    def get(name):
        result = Dropdown.objects.filter(name=name)
        if result:
            return result[0]
        else:
            logger.error(u'Could not find dropdown with name "{}". Please create it using /admin/'.format(name))
            return DummyDropdown(name)


class Choice(models.Model):
    dropdown = models.ForeignKey(Dropdown, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    value = models.CharField(max_length=255)

    def as_choice(self):
        return (self.value, self.name)

    def __str__(self):
        return u"{} ({})".format(self.name, self.dropdown.name)

class SwitchType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    ports = models.CharField(max_length=1024, blank=True, help_text="Autogenerate ports. \",\" separator. ex: Et1,Et2,Et3 alt ge-0/0/0,ge-0/0/1")

    @classmethod
    def as_choices(self):
        choices=[('','')]
        choices.extend([(val.pk, val.name) for val in SwitchType.objects.all()])
        return choices

    def __str__(self):
        return "{}".format(self.name)


# -- Signals
@receiver(comment_was_posted, dispatch_uid="apps.noclook.models")
def comment_posted_handler(sender, comment, request, **kwargs):
    action.send(
        comment.user,
        verb='create',
        action_object=comment,
        target=comment.content_object,
        noclook={
            'action_type': 'comment',
        }
    )


@receiver(comment_was_flagged, dispatch_uid="apps.noclook.models")
def comment_removed_handler(sender, comment, flag, created, request, **kwargs):
    action.send(
        comment.user,
        verb='delete',
        action_object=comment,
        target=comment.content_object,
        noclook={
            'action_type': 'comment',
            'comment': comment.comment,
        }
    )
