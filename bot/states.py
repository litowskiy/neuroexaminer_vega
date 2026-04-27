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
