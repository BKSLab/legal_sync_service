import json
from datetime import UTC

from sqladmin.fields import DateTimeField, JSONField
from wtforms import Form


class AdminForm(Form):
    def __init__(self, formdata=None, *args, **kwargs):
        # SQLAdmin передаёт пустую FormData даже на GET. WTForms считает её
        # отправленной формой и сбрасывает значение checkbox по умолчанию.
        super().__init__(formdata or None, *args, **kwargs)

    class Meta:
        locales = ["ru"]


class AdminJSONField(JSONField):
    """Пустой список и отсутствие значения сохраняют свой тип при редактировании."""

    def _value(self) -> str:
        if self.raw_data:
            return self.raw_data[0]
        if self.data is None:
            return ""
        return json.dumps(self.data, ensure_ascii=False, indent=2)


class UTCDateTimeField(DateTimeField):
    """Форма показывает и принимает время в явно подписанной зоне UTC."""

    def process_data(self, value):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        super().process_data(value)

    def process_formdata(self, valuelist):
        super().process_formdata(valuelist)
        if self.data is not None:
            self.data = self.data.replace(tzinfo=UTC)
