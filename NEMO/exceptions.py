from typing import List

from NEMO.models import User, Area, Resource, Interlock


class NEMOException(Exception):
	""" Basic NEMO exception """

	default_msg = "A NEMO error occurred"

	def __init__(self, msg=None):
		if msg is None:
			msg = self.default_msg
		self.msg = msg
		super(NEMOException, self).__init__(msg)


class InterlockError(NEMOException):
	""" Interlock related errors """

	def __init__(self, interlock: Interlock, msg=None):
		self.message = msg
		error_message = f"An Error occurred with interlock [{interlock}]"
		if msg is not None:
			error_message += f": {msg}"
		self.interlock = interlock
		super(InterlockError, self).__init__(error_message)


class UserAccessError(NEMOException):
	""" User access related errors """

	detailed_msg = ""

	def __init__(self, user: User, msg=None):
		message = f"An Error occurred with user access [{user}]"
		if msg is not None:
			message += f": {msg}"
		elif self.detailed_msg:
			message += f": {self.detailed_msg}"
		self.user = user
		super(UserAccessError, self).__init__(message)


class InactiveUserError(UserAccessError):
	detailed_msg = "This user is not active"


class NoActiveProjectsForUserError(UserAccessError):
	detailed_msg = "This user does not have any active projects"


class PhysicalAccessExpiredUserError(UserAccessError):
	detailed_msg = "This user's physical access has expired"


class NoPhysicalAccessUserError(UserAccessError):
	detailed_msg = "This user has not been granted physical access to any area"


class NoAccessiblePhysicalAccessUserError(UserAccessError):

	def __init__(self, user: User, area:Area):
		details = f"This user is not assigned to a physical access that allow access to this area [{area}] at this time"
		super(NoAccessiblePhysicalAccessUserError).__init__(user=user, msg=details)


class UnavailableResourcesUserError(UserAccessError):
	def __init__(self, user:User, area:Area, resources: List[Resource]):
		details = f"This user was blocked from entering this area [{area}] because a required resource was unavailable [{resources}"
		super(NoAccessiblePhysicalAccessUserError).__init__(user=user, msg=details)