from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

import django_filters
from funfactory.urlresolvers import reverse
from rest_framework import viewsets, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from mozillians.common.helpers import absolutify, markdown
from mozillians.users.managers import PUBLIC
from mozillians.users.models import UserProfile, ExternalAccount, Language


### Serializers

class UserProfileSerializer(serializers.HyperlinkedModelSerializer):
    username = serializers.Field(source='user.username')

    class Meta:
        model = UserProfile
        fields = ('username', '_url', 'is_vouched')


class ExternalAccountSerializer(serializers.ModelSerializer):
    privacy = serializers.CharField(source='get_privacy_display')

    class Meta:
        model = ExternalAccount
        fields = ('type', 'identifier', 'privacy')

    def transform_type(self, obj, value):
        return value.lower()


class LanguageSerializer(serializers.ModelSerializer):
    english = serializers.CharField(source='get_english')
    native = serializers.CharField(source='get_native')

    class Meta:
        model = Language
        fields = ('code', 'english', 'native')


class UserProfileDetailedSerializer(serializers.HyperlinkedModelSerializer):
    username = serializers.Field(source='user.username')
    email = serializers.Field(source='user.email')
    external_accounts = ExternalAccountSerializer(many=True, source='externalaccount_set')
    languages = LanguageSerializer(many=True, source='language_set')
    is_public = serializers.Field(source='is_public')
    url = serializers.SerializerMethodField('get_url')

    # Add profile URL

    class Meta:
        model = UserProfile
        fields = ('username', 'full_name', 'is_vouched', 'email',
                  'date_vouched', 'vouched_by', 'bio', 'photo',
                  'ircname', 'country', 'region', 'city',
                  'date_mozillian', 'timezone', 'title', 'story_link',
                  'languages', 'external_accounts', '_url', 'tshirt',
                  'is_public', 'url')

    def _transform_privacy_wrapper(self, field):
        field = field

        def _transform_privacy(obj, value):
            return {
                'value': value,
                'privacy': getattr(obj, 'get_privacy_{0}_display'.format(field))()
            }
        return _transform_privacy

    def __init__(self, *args, **kwargs):
        super(UserProfileDetailedSerializer, self).__init__(*args, **kwargs)

        for field in self.fields.keys():
            method_name = 'transform_{0}'.format(field)

            # If we don't define a custom transform method and if the
            # field has a privacy setting, set the transform privacy
            # wrapper.
            # TODO
            if ((not getattr(self, method_name, None) and
                 getattr(UserProfile, 'get_privacy_{0}_display'.format(field), None))):
                setattr(self, method_name, self._transform_privacy_wrapper(field))

    def get_url(self, obj):
        return absolutify(reverse('phonebook:profile_view', kwargs={'username': obj.user.username}))

    def transform_timezone(self, obj, value):
        return {
            'value': value,
            'utc_offset': obj.timezone_offset(),
            'privacy': obj.get_privacy_timezone_display(),
        }

    def transform_bio(self, obj, value):
        return {
            'value': value,
            'html': markdown(value),
            'privacy': obj.get_privacy_bio_display(),
        }

    def transform_photo(self, obj, value):
        return {
            'value': obj.get_photo_url('300x300'),
            '150x150': obj.get_photo_url('150x150'),
            '300x300': obj.get_photo_url('300x300'),
            '500x500': obj.get_photo_url('500x500'),
            'privacy': obj.get_privacy_photo_display(),
        }

    def transform_country(self, obj, value):
        country = obj.geo_country
        return {
            'value': country.code if country else None,
            'english': country.name if country else None,
            'privacy': obj.get_privacy_geo_country_display(),
        }

    def transform_region(self, obj, value):
        region = obj.geo_region
        return {
            'value': region.code if region else None,
            'english': region.name if region else None,
            'privacy': obj.get_privacy_geo_region_display(),
        }

    def transform_city(self, obj, value):
        city = obj.geo_city
        return {
            'value': city.code if city else None,
            'english': city.name if city else None,
            'privacy': obj.get_privacy_geo_city_display(),
        }

    def transform_tshirt(self, obj, value):
        return {
            'value': obj.tshirt,
            'english': obj.get_tshirt_display(),
            'privacy': obj.get_privacy_tshirt_display(),
        }


## Filters

class UserProfileFilter(django_filters.FilterSet):
    city = django_filters.CharFilter(name='geo_city__name')
    region = django_filters.CharFilter(name='geo_region__name')
    country = django_filters.CharFilter(name='geo_country__name')
    country_code = django_filters.CharFilter(name='geo_country__code')

    class Meta:
        model = UserProfile
        fields = ('is_vouched', 'vouched_by', 'city', 'region', 'country',
                  'country_code', 'timezone', 'tshirt')


### Views
class UserProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """ Foo *bar* TODO."""
    serializer_class = UserProfileSerializer
    model = UserProfile
    filter_class = UserProfileFilter
    ordering = ('user__username',)

    def get_queryset(self):
        queryset = UserProfile.objects.complete()
        if self.request.privacy_level == PUBLIC:
            queryset = queryset.public()
        queryset = queryset.privacy_level(self.request.privacy_level)
        return queryset

    def retrieve(self, request, pk):
        user = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = UserProfileDetailedSerializer(user)
        return Response(serializer.data)


class LookUpUser(APIView):

    def __init__(self, *args, **kwargs):
        super(LookUpUser, self).__init__(*args, **kwargs)
        self.available_filters = ['username', 'email']
        for account_type in ExternalAccount.ACCOUNT_TYPES.keys():
            filtr = 'account_{0}'.format(account_type.lower())
            self.available_filters.append(filtr)

    def get_queryset(self):
        queryset = UserProfile.objects.complete().privacy_level(self.request.privacy_level)
        # Prepare a queryset like
        # users.managers.UserProfileQuerySet.public_q with
        # self.request.privacy_level
        return queryset
        
    def get(self, request, format=None):
        filtr, value = request.GET.items()[0]

        if filtr == 'username':
            query = Q(user__username=value)

        elif filtr == 'email':
            query = Q(user__email=value, privacy_email__gte=self.request.privacy_level)

        elif filtr.startswith('account_'):
            account_type = filtr[8:]
            # Edo tha mporouse kapoios na exi to account PUBLIC kai to
            # idio idnetifier me kapoion allo pou exi to accoun
            # private. To apotelesma tha itan se ena PUBLIC request na
            # diksoume mono ton enan

            query = Q(externalaccount__type=account_type,
                      externalaccount__identifier=value,
                      externalaccount__privacy__gte=self.request.privacy_level)
        else:
            # Deal with this
            raise Http404

        try:
            user = self.get_queryset().get(query)
        except UserProfile.MultipleObjectsReturned:
            # Deal with this
            raise Http404
        except UserProfile.DoesNotExist:
            # Deal with this
            raise Http404

        serializer = UserProfileDetailedSerializer(user)
        return Response(serializer.data)


class LanguageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Language.objects.all()
    serializer_class = LanguageSerializer
    ordering_fields = tuple()
