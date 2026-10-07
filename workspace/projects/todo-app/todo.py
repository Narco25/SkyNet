tasks = []
task_template = {
    'id': None,
    'description': '',
    'completed': False
}

def add_task(description):
    task = task_template.copy()
    task['id'] = len(tasks) + 1
    task['description'] = description
    tasks.append(task)


def list_tasks():
    for task in tasks:
        status = 'Completed' if task['completed'] else 'Pending'
        print(f"ID: {task['id']}, Description: {task['description']}, Status: {status}")


def complete_task(task_id):
    for task in tasks:
        if task['id'] == task_id:
            task['completed'] = True
            print(f"Task ID {task_id} marked as completed.")
            return
    print(f"Task ID {task_id} not found.")