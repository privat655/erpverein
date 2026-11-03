from erpverein.services.work_queue_service import get_work_queue


def execute(filters=None):
	return get_work_queue(filters)
