from aiogram.fsm.state import State, StatesGroup


class NewOrder(StatesGroup):
    name = State()
    phone = State()
    address = State()
    location = State()
    count = State()
    dims = State()
    ctype = State()
    instr = State()
    price = State()
    confirm = State()


class AddEmp(StatesGroup):
    tid = State()
    role = State()
    name = State()
    salary_type = State()
    base = State()
    confirm = State()


class EmpEdit(StatesGroup):
    value = State()


class Pay(StatesGroup):
    amount = State()
    method = State()
    note = State()
    confirm = State()


class Search(StatesGroup):
    query = State()


class SetVal(StatesGroup):
    value = State()


class FinalPrice(StatesGroup):
    value = State()
