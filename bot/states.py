from aiogram.fsm.state import State, StatesGroup


class UploadStates(StatesGroup):
    waiting_for_file = State()


class QuizSetupStates(StatesGroup):
    selecting_mode = State()
    selecting_mixed_formats = State()
    selecting_options_count = State()
    selecting_count = State()


class QuizStates(StatesGroup):
    in_session = State()


class ChatStates(StatesGroup):
    in_chat = State()


class TeacherStates(StatesGroup):
    waiting_auth_code = State()
    waiting_fio = State()
    waiting_subject = State()
    waiting_group_name = State()


class JoinGroupStates(StatesGroup):
    waiting_code = State()
    waiting_fio = State()
