# -*- coding: utf-8 -*-

# Standard library imports
from __future__ import unicode_literals

# Third party imports
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _
from model_utils import Choices
from mptt.models import MPTTModel
from mptt.models import TreeForeignKey

# Local application / specific library imports
from machina.conf import settings as machina_settings
from machina.core.compat import AUTH_USER_MODEL
from machina.models import ActiveModel
from machina.models import DatedModel
from machina.models.fields import ExtendedImageField
from machina.models.fields import MarkupTextField


FORUM_TYPES = Choices(
    (0, 'forum_post', _('Default forum')),
    (1, 'forum_cat', _('Category forum')),
    (2, 'forum_link', _('Link forum')),
)


@python_2_unicode_compatible
class AbstractForum(MPTTModel, ActiveModel):
    """
    The main forum model.
    The tree hierarchy of forums and categories is managed by the MPTTModel
    which is part of django-mptt.
    """
    parent = TreeForeignKey('self', null=True, blank=True, related_name='children', verbose_name=_('Parent'))

    name = models.CharField(max_length=100, verbose_name=_('Name'))
    description = MarkupTextField(verbose_name=_('Description'), null=True, blank=True)

    # A forum can come with an image (eg. a small logo)
    image = ExtendedImageField(verbose_name=_('Forum image'), null=True, blank=True,
                               upload_to=machina_settings.FORUM_IMAGE_UPLOAD_TO,
                               **machina_settings.DEFAULT_FORUM_IMAGE_SETTINGS)

    # Forums can be simple links (eg. wiki, documentation, etc)
    link = models.URLField(verbose_name=_('Forum link'), null=True, blank=True)
    link_redirects = models.BooleanField(verbose_name=_('Track link redirects count'),
                                         help_text=_('Records the number of times a forum link was clicked'),
                                         default=False)

    # Category, Default forum or Link ; that's what a forum can be
    type = models.PositiveSmallIntegerField(choices=FORUM_TYPES, verbose_name=_('Forum type'), db_index=True)

    # Tracking data
    posts_count = models.PositiveIntegerField(verbose_name=_('Number of posts'), editable=False, blank=True, default=0)
    topics_count = models.PositiveIntegerField(verbose_name=_('Number of topics'), editable=False, blank=True, default=0)
    real_topics_count = models.PositiveIntegerField(verbose_name=_('Number of topics (includes unapproved topics)'),
                                                    editable=False, blank=True, default=0)
    link_redirects_count = models.PositiveIntegerField(verbose_name=_('Track link redirects count'),
                                                       editable=False, blank=True, default=0)

    # Display options
    display_on_index = models.BooleanField(verbose_name=_('Display on index'), default=True)
    display_sub_forum_list = models.BooleanField(verbose_name=_('Display sub forum list'), default=True)

    class Meta:
        abstract = True
        ordering = ['tree_id', 'lft']
        verbose_name = _('Forum')
        verbose_name_plural = _('Forums')
        app_label = 'forum'

    def __str__(self):
        return '{}'.format(self.name)

    @property
    def margin_level(self):
        """
        Used in templates or menus to create an easy-to-see left margin to contrast
        a forum from their parents.
        """
        return self.level * 2

    @property
    def is_category(self):
        return self.type == FORUM_TYPES.forum_cat

    @property
    def is_forum(self):
        return self.type == FORUM_TYPES.forum_post

    @property
    def is_link(self):
        return self.type == FORUM_TYPES.forum_link

    def clean(self):
        if self.parent:
            if self.parent.type == FORUM_TYPES.forum_link:
                raise ValidationError(_('A forum can not have a link forum as parent'))

        super(AbstractForum, self).clean()

    def save(self, *args, **kwargs):
        # It is vital to track the changes of the parent associated with a forum in order to
        # maintain counters up-to-date and to trigger other operations such as permissions updates.
        old_instance = None
        if self.pk:
            old_instance = self.__class__._default_manager.get(pk=self.pk)

        # Do the save
        super(AbstractForum, self).save(*args, **kwargs)

        # If any change has been made to the forum parent, trigger the update of the counters
        if old_instance and old_instance.parent != self.parent:
            self.update_trackers()
            # TODO: trigger a 'post_forum_parent_update' signal

    def _simple_save(self, *args, **kwargs):
        """
        Calls the parent save method in order to avoid the checks for forum parent changes
        which can result in triggering a new update of the counters associated with the
        current forum.
        This allow the database to not be hit by such checks during very common and regular
        operations such as those provided by the update_trackers function; indeed these operations
        will never result in an update of a forum parent.
        """
        super(AbstractForum, self).save(*args, **kwargs)

    def update_trackers(self):
        # Fetch the list of ids of all descendant forums including the current one
        forum_ids = self.get_descendants(include_self=True).values_list('id', flat=True)

        # Determine the list of the associated topics, that is the list of topics
        # associated with the current forum plus the list of all topics associated
        # with the descendant forums.
        Topic = models.get_model('conversation', 'Topic')
        topics = Topic.objects.filter(forum__id__in=forum_ids)

        self.real_topics_count = topics.count()
        self.topics_count = topics.filter(approved=True).count()
        # Compute the forum level posts count
        posts_count = sum(topic.posts_count for topic in topics)
        self.posts_count = posts_count

        # Any save of a forum triggered from the update_tracker process will not result
        # in checking for a change of the forum's parent.
        self._simple_save()

        # Trigger the parent trackers update if necessary
        if self.parent:
            self.parent.update_trackers()


@python_2_unicode_compatible
class AbstractForumReadTrack(DatedModel):
    """
    Represents a track which records which forums have been read by a given user.
    """
    user = models.ForeignKey(AUTH_USER_MODEL, related_name='forum_tracks', verbose_name=_('User'))
    forum = models.ForeignKey('forum.Forum', verbose_name=_('Forum'), related_name='tracks')

    class Meta:
        abstract = True
        unique_together = ['user', 'forum', ]
        verbose_name = _('Forum track')
        verbose_name_plural = _('Forum tracks')
        app_label = 'forum'

    def __str__(self):
        return '{} - {}'.format(self.user, self.topic)