from _ssl import PROTOCOL_TLSv1_2, CERT_REQUIRED
from base64 import b64decode
from logging import getLogger

from django.conf import settings
from django.contrib.auth import authenticate, login, REDIRECT_FIELD_NAME, logout
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.middleware import RemoteUserMiddleware
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render
from django.urls import reverse, resolve
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_GET
from ldap3 import Tls, Server, Connection, AUTO_BIND_TLS_BEFORE_BIND, SIMPLE, AUTO_BIND_NO_TLS, ANONYMOUS
from ldap3.core.exceptions import LDAPBindError, LDAPException

from NEMO.exceptions import InactiveUserError
from NEMO.middleware import HTTPHeaderAuthenticationMiddleware, RemoteUserAuthenticationMiddleware
from NEMO.models import User
from NEMO.views.customization import get_media_file_contents

auth_logger = getLogger(__name__)


def get_full_class_name(clas):
	return clas.__module__ + "." + clas.__name__


def check_user_exists_and_active(backend: ModelBackend, username: str) -> User:
	# The user must exist in the database
	try:
		user = User.objects.get(username=username)
	except User.DoesNotExist:
		auth_logger.warning(f"Username {username} attempted to authenticate with {type(backend).__name__}, but that username does not exist in the NEMO database. The user was denied access.")
		raise
	# The user must be marked active.
	if not user.is_active:
		auth_logger.warning(f"User {username} successfully authenticated with {type(backend).__name__}, but that user is marked inactive in the NEMO database. The user was denied access.")
		raise InactiveUserError(user=username)
	# All security checks passed so let the user in.
	auth_logger.debug(f"User {username} successfully authenticated with {type(backend).__name__} and was granted access to NEMO.")
	return user


def check_pre_authentication_backends(request):
	if get_full_class_name(RemoteUserAuthenticationBackend) in settings.AUTHENTICATION_BACKENDS or get_full_class_name(NginxKerberosAuthorizationHeaderAuthenticationBackend) in settings.AUTHENTICATION_BACKENDS:
		# check for improper configuration
		if get_full_class_name(RemoteUserMiddleware) not in settings.MIDDLEWARE \
				and get_full_class_name(HTTPHeaderAuthenticationMiddleware) not in settings.MIDDLEWARE \
				and get_full_class_name(RemoteUserAuthenticationMiddleware) not in settings.MIDDLEWARE :
			auth_logger.error("To use this backend you need to add either RemoteUserMiddleware, RemoteUserAuthenticationMiddleware or HTTPHeaderAuthenticationMiddleware to settings.MIDDLEWARE")
			return HttpResponse("There was an error pre-authenticating the user", status=400)
		# if the user is already in the request and authenticated, send to landing page
		elif hasattr(request, 'user') and request.user.is_authenticated:
			return HttpResponseRedirect(reverse('landing'))
		else:
			return HttpResponse("There was an error pre-authenticating the user", status=400)


class RemoteUserAuthenticationBackend(ModelBackend):
	""" The web server performs Kerberos authentication and passes the user name in via the REMOTE_USER environment variable. """

	def authenticate(self, request, remote_user):
		if not remote_user:
			return
		username = self.clean_username(remote_user)
		return check_user_exists_and_active(self, username)


	def clean_username(self, username):
		"""
		User names arrive in the form user@DOMAIN.NAME.
		This function chops off Kerberos realm information (i.e. the '@' and everything after).
		"""
		return username.partition('@')[0]


class NginxKerberosAuthorizationHeaderAuthenticationBackend(RemoteUserAuthenticationBackend):
	""" The web server performs Kerberos authentication and passes the user name in via the HTTP_AUTHORIZATION header. """

	def clean_username(self, username):
		"""
		User names arrive encoded in base 64, similar to Basic authentication, but with a bogus password set (since .
		This function chops off Kerberos realm information (i.e. the '@' and everything after).
		"""
		if not username:
			return None
		pieces = username.split()
		if len(pieces) != 2:
			return None
		if pieces[0] != "Basic":
			return None
		return b64decode(pieces[1]).decode().partition(':')[0]


class LDAPAuthenticationBackend(ModelBackend):
	""" This class provides LDAP authentication against an LDAP or Active Directory server. """

	@method_decorator(sensitive_post_parameters('password'))
	def authenticate(self, request, username=None, password=None, **keyword_arguments):
		if not username or not password:
			return None

		user = check_user_exists_and_active(self, username)

		is_authenticated_with_ldap = False
		errors = []
		for server in settings.LDAP_SERVERS:
			try:
				port = server.get('port', 636)
				use_ssl = server.get('use_ssl', True)
				bind_as_authentication = server.get('bind_as_authentication', True)
				domain = server.get('domain')
				t = Tls(validate=CERT_REQUIRED, version=PROTOCOL_TLSv1_2, ca_certs_file=server.get('certificate'))
				s = Server(server['url'], port=port, use_ssl=use_ssl, tls=t)
				auto_bind = AUTO_BIND_TLS_BEFORE_BIND if use_ssl else AUTO_BIND_NO_TLS
				ldap_bind_user = f"{domain}\\{username}" if domain else username
				if not bind_as_authentication:
					# binding to LDAP first, then search for user
					bind_username = server.get('bind_username', None)
					bind_username = f"{domain}\\{bind_username}" if domain and bind_username else bind_username
					bind_password = server.get('bind_password', None)
					authentication = SIMPLE if bind_username and bind_password else ANONYMOUS
					c = Connection(s, user=bind_username, password=bind_password, auto_bind=auto_bind, authentication=authentication, raise_exceptions=True)
					search_username_field = server.get('search_username_field', 'uid')
					search_attribute = server.get('search_attribute', 'cn')
					search = c.search(server['base_dn'], f"({search_username_field}={username})", attributes=[search_attribute])
					if not search or search_attribute not in c.response[0].get('attributes', []):
						# no results, unbind and continue to next server
						c.unbind()
						errors.append(f"User {username} attempted to authenticate with LDAP ({server['url']}), but the search with dn:{server['base_dn']}, username_field:{search_username_field} and attribute:{search_attribute} did not return any results. The user was denied access")
						continue
					else:
						# we got results, get the dn that will be used for binding authentication
						response = c.response[0]
						ldap_bind_user = response['dn']
						c.unbind()

				# let's proceed with binding using the user trying to authenticate
				c = Connection(s, user=ldap_bind_user, password=password, auto_bind=auto_bind, authentication=SIMPLE, raise_exceptions=True)
				c.unbind()
				# At this point the user successfully authenticated to at least one LDAP server.
				is_authenticated_with_ldap = True
				auth_logger.debug(f"User {username} was successfully authenticated with LDAP ({server['url']})")
				break
			except LDAPBindError as e:
				errors.append(f"User {username} attempted to authenticate with LDAP ({server['url']}), but entered an incorrect password. The user was denied access: {str(e)}")
			except LDAPException as e:
				errors.append(f"User {username} attempted to authenticate with LDAP ({server['url']}), but an error occurred. The user was denied access: {str(e)}")

		if is_authenticated_with_ldap:
			return user
		else:
			for error in errors:
				auth_logger.warning(error)
			return None


@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('password')
def login_user(request):
	# check to make sure we don't have a misconfiguration. pre-authentication backends need to use middleware
	response = check_pre_authentication_backends(request)
	if response:
		return response

	dictionary = {
		'login_banner': get_media_file_contents('login_banner.html'),
		'user_name_or_password_incorrect': False,
	}

	# if we are dealing with anything else than POST, send to login page
	if request.method != 'POST':
		return render(request, 'login.html', dictionary)
	# Otherwise try to log the user in
	else:
		username = request.POST.get('username', '')
		password = request.POST.get('password', '')
		try:
			user = authenticate(request, username=username, password=password)
		except (User.DoesNotExist, InactiveUserError):
			return authorization_failed(request)

		if user:
			login(request, user)
			try:
				next_page = request.GET[REDIRECT_FIELD_NAME]
				resolve(next_page)  # Make sure the next page is a legitimate URL for NEMO
			except:
				next_page = reverse('landing')
			return HttpResponseRedirect(next_page)
		dictionary['user_name_or_password_incorrect'] = True
		return render(request, 'login.html', dictionary)


@require_GET
def logout_user(request):
	logout(request)
	return HttpResponseRedirect(reverse('landing'))


def authorization_failed(request):
	authorization_page = get_media_file_contents('authorization_failed.html')
	return render(request, 'authorization_failed.html', {'authorization_failed': authorization_page})
