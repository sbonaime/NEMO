from datetime import timedelta
from typing import List

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import F
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from NEMO.exceptions import NoAccessiblePhysicalAccessUserError, UnavailableResourcesUserError, UserAccessError, \
	InactiveUserError, NoActiveProjectsForUserError, NoPhysicalAccessUserError, PhysicalAccessExpiredUserError, \
	MaximumCapacityReachedError
from NEMO.models import Area, AreaAccessRecord, Project, User, PhysicalAccessLevel

from NEMO.utilities import parse_start_and_end_date
from NEMO.views.customization import get_customization
from NEMO.views.policy import check_policy_to_enter_this_area, check_policy_to_enter_any_area


@staff_member_required(login_url=None)
@require_GET
def area_access(request):
	""" Presents a page that displays audit records for all NanoFab areas. """
	now = timezone.now().astimezone()
	today = now.strftime('%m/%d/%Y')
	yesterday = (now - timedelta(days=1)).strftime('%m/%d/%Y')
	dictionary = {
		'today': reverse('area_access') + '?' + urlencode({'start': today, 'end': today}),
		'yesterday': reverse('area_access') + '?' + urlencode({'start': yesterday, 'end': yesterday}),
		'areas': Area.objects.all(),
	}
	try:
		start, end = parse_start_and_end_date(request.GET['start'], request.GET['end'])
		area = request.GET.get('area')
		dictionary['start'] = start
		dictionary['end'] = end
		area_access_records = AreaAccessRecord.objects.filter(start__gte=start, start__lt=end, staff_charge=None)
		if area:
			area_access_records = area_access_records.filter(area__name=area)
			dictionary['area_name'] = area
		area_access_records = area_access_records.order_by('area__name')
		area_access_records.query.add_ordering(F('end').desc(nulls_first=True))
		area_access_records.query.add_ordering(F('start').desc())
		dictionary['access_records'] = area_access_records
	except:
		pass
	return render(request, 'area_access/area_access.html', dictionary)


@staff_member_required(login_url=None)
@require_http_methods(['GET', 'POST'])
def new_area_access_record(request):
	dictionary = {
		'customers': User.objects.filter(is_active=True)
	}
	if request.method == 'GET':
		try:
			customer = User.objects.get(id=request.GET['customer'])
			dictionary['customer'] = customer
			error_message = check_policy_for_user(customer=customer)
			if error_message:
				dictionary['error_message'] = error_message
				return render(request, 'area_access/new_area_access_record.html', dictionary)

			dictionary['areas'] = list(get_accessible_areas_for_user(user=customer))
			return render(request, 'area_access/new_area_access_record_details.html', dictionary)
		except:
			pass
		return render(request, 'area_access/new_area_access_record.html', dictionary)
	if request.method == 'POST':
		try:
			user = User.objects.get(id=request.POST['customer'])
			project = Project.objects.get(id=request.POST['project'])
			area = Area.objects.get(id=request.POST['area'])
		except:
			dictionary['error_message'] = 'Your request contained an invalid identifier.'
			return render(request, 'area_access/new_area_access_record.html', dictionary)
		try:
			error_message = check_policy_for_user(customer=user)
			if error_message:
				dictionary['error_message'] = error_message
				return render(request, 'area_access/new_area_access_record.html', dictionary)
			check_policy_to_enter_this_area(area=area, user=user)
		except NoAccessiblePhysicalAccessUserError:
			dictionary['error_message'] = '{} does not have a physical access level that allows access to the {} at this time.'.format(user, area.name.lower())
			return render(request, 'area_access/new_area_access_record.html', dictionary)
		except UnavailableResourcesUserError:
			dictionary['error_message'] = 'The {} is inaccessible because a required resource is unavailable. You must make all required resources for this area available before creating a new area access record.'.format(area.name.lower())
			return render(request, 'area_access/new_area_access_record.html', dictionary)
		except MaximumCapacityReachedError:
			dictionary['error_message'] = 'The {} is inaccessible because it has reached its maximum capacity. Wait for somebody to exit and try again.'.format(area.name.lower())
			return render(request, 'area_access/new_area_access_record.html', dictionary)
		if user.billing_to_project():
			dictionary['error_message'] = '{} is already billing area access to another area. The user must log out of that area before entering another.'.format(user)
			return render(request, 'area_access/new_area_access_record.html', dictionary)
		if project not in user.active_projects():
			dictionary['error_message'] = '{} is not authorized to bill that project.'.format(user)
			return render(request, 'area_access/new_area_access_record.html', dictionary)
		record = AreaAccessRecord()
		record.area = area
		record.customer = user
		record.project = project
		record.save()
		dictionary['success'] = '{} is now logged in to the {}.'.format(user, area.name.lower())
		return render(request, 'area_access/new_area_access_record.html', dictionary)


def check_policy_for_user(customer: User):
	error_message = None
	try:
		check_policy_to_enter_any_area(user=customer)
	except InactiveUserError:
		error_message = '{} is inactive'.format(customer)
	except NoActiveProjectsForUserError:
		error_message = '{} does not have any active projects to bill area access'.format(customer)
	except NoPhysicalAccessUserError:
		error_message = '{} does not have access to any billable NanoFab areas'.format(customer)
	except PhysicalAccessExpiredUserError:
		error_message = '{} does not have access to any areas because the user\'s physical access expired on {}. You must update the user\'s physical access expiration date before creating a new area access record.'.format(customer, customer.access_expiration.strftime('%B %m, %Y'))
	return error_message


@staff_member_required(login_url=None)
@require_POST
def force_area_logout(request, user_id):
	user = get_object_or_404(User, id=user_id)
	record = user.area_access_record()
	if record is None:
		return HttpResponseBadRequest('That user is not logged into any areas.')
	record.end = timezone.now()
	record.save()
	return HttpResponse()


@login_required
@require_http_methods(['GET', 'POST'])
def change_project(request, new_project=None):
	user: User = request.user
	""" For area access, allow the user to stop billing a project and start billing another project. """
	if request.method == 'GET':
		return render(request, 'area_access/change_project.html')
	old_project = user.billing_to_project()
	if old_project is None:
		dictionary = {
			'error': "There was a problem changing the project you're billing for area access. You must already be billing a project in order to change to another project, however you were not logged in to an area."
		}
		return render(request, 'area_access/change_project.html', dictionary)
	# If we're already billing the requested project then there's nothing to do.
	if old_project.id == new_project:
		return redirect(reverse('landing'))
	new_project = get_object_or_404(Project, id=new_project)
	if new_project not in user.active_projects():
		dictionary = {
			'error': 'You do not have permission to bill that project.'
		}
		return render(request, 'area_access/change_project.html', dictionary)
	# Stop billing the user's initial project
	record = user.area_access_record()
	record.end = timezone.now()
	record.save()
	area = record.area
	# Start billing the user's new project
	record = AreaAccessRecord()
	record.area = area
	record.customer = request.user
	record.project = new_project
	record.save()
	return redirect(reverse('landing'))


@login_required
@require_http_methods(['GET', 'POST'])
def self_log_in(request):
	user: User = request.user
	if not able_to_self_log_in_to_area(user):
		return redirect(reverse('landing'))
	dictionary = {
		'projects': user.active_projects(),
	}
	areas = []
	for access_level in get_accessible_areas_for_user(user):
		unavailable_resources = access_level.area.required_resources.filter(available=False)
		if access_level.accessible() and not unavailable_resources:
			areas.append(access_level.area)
	dictionary['areas'] = areas
	if request.method == 'GET':
		return render(request, 'area_access/self_login.html', dictionary)
	if request.method == 'POST':
		try:
			a = Area.objects.get(id=request.POST['area'])
			p = Project.objects.get(id=request.POST['project'])
			check_policy_to_enter_this_area(a, request.user)
			if a in dictionary['areas'] and p in dictionary['projects']:
				AreaAccessRecord.objects.create(area=a, customer=request.user, project=p)
		except MaximumCapacityReachedError:
			dictionary['error_message'] = 'The {} is inaccessible because it has reached its maximum capacity. Wait for somebody to exit and try again.'.format(a.name.lower())
			return render(request, 'area_access/self_login.html', dictionary)
		except:
			dictionary['error_message'] = "error"
			return render(request, 'area_access/self_login.html', dictionary)
		return redirect(reverse('landing'))


@login_required
@require_GET
def self_log_out(request, user_id):
	user = get_object_or_404(User, id=user_id)
	if able_to_self_log_out_of_area(user):
		record = user.area_access_record()
		if record is None:
			return HttpResponseBadRequest('You are not logged into any areas.')
		record.end = timezone.now()
		record.save()
	return redirect(reverse('landing'))


def able_to_self_log_out_of_area(user):
	# 'Self log out' must be enabled
	if not get_customization('self_log_out') == 'enabled':
		return False
	# Check if the user is active
	if not user.is_active:
		return False
	# Check if the user is already in an area.
	if not user.in_area():
		return False
	# Otherwise we are good to log out
	return True


def able_to_self_log_in_to_area(user):
	# 'Self log in' must be enabled
	if not get_customization('self_log_in') == 'enabled':
		return False
	# Check if the user is already in an area. If so, the /change_project/ URL can be used to change their project.
	if user.in_area():
		return False
	# Check policy for entering an area
	try:
		check_policy_to_enter_any_area(user=user)
	except UserAccessError:
		return False

	accessible_areas = get_accessible_areas_for_user(user)

	# Check if the user normally has access to an area at the current time
	for accessible_area in accessible_areas:
		try:
			check_policy_to_enter_this_area(area=accessible_area.area, user=user)
			# Users may not access an area if it's not accessible at this time or if a required resource is unavailable,
			# so return true if there exists at least one area they are able to log in to.
			return True
		except (NoAccessiblePhysicalAccessUserError, UnavailableResourcesUserError):
			pass
		except MaximumCapacityReachedError:
			# we don't want to deal with that error just yet. we'll let the user try to log in and get an error
			return True

	# No areas are accessible...
	return False


def get_accessible_areas_for_user(user: User) -> List[PhysicalAccessLevel]:
	accessible_areas = set(user.physical_access_levels.all())
	# If staff user, add areas where physical access level allow staff access directly
	if user.is_staff:
		accessible_areas.update(PhysicalAccessLevel.objects.filter(allow_staff_access=True))

	return accessible_areas
