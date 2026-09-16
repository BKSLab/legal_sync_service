/* Элементы управления админкой. Подписи и сообщения — на русском. */
$(function () {
    const checks = $('.select-box');
    const bulkDelete = $('#action-delete');
    function updateSelection() {
        const count = checks.filter(':checked').length;
        bulkDelete.prop('disabled', count === 0);
        $('#selection-count').text(count ? '(' + count + ')' : '');
        $('#select-all').prop('checked', count > 0 && count === checks.length)
            .prop('indeterminate', count > 0 && count < checks.length);
    }
    checks.on('change', updateSelection);
    $('#select-all').on('change', function () {
        checks.prop('checked', this.checked);
        updateSelection();
    });
    bulkDelete.on('click', function () {
        const selected = checks.filter(':checked').map(function () { return this.value; }).get();
        if (!selected.length) return;
        const url = new URL(this.dataset.url, window.location.origin);
        url.searchParams.set('pks', selected.join(','));
        $(this).data('pk', selected.join(',')).data('deleteUrl', url.toString());
        $('#modal-delete').modal('show', this);
    });
    $('#modal-delete').on('show.bs.modal', function (event) {
        const trigger = $(event.relatedTarget);
        const ids = String(trigger.data('pk')).split(',');
        $('#modal-delete-text').text(ids.length === 1
            ? 'Удалить запись №' + ids[0] + '? Это действие нельзя отменить.'
            : 'Удалить выбранные записи (' + ids.length + ')? Это действие нельзя отменить.');
        $('#modal-delete-button').data('url', trigger.data('deleteUrl') || trigger.data('url'));
        $('#delete-error').prop('hidden', true).text('');
    });
    $('#modal-delete-button').on('click', async function () {
        const button = $(this);
        const url = button.data('url');
        if (!url) return;
        button.prop('disabled', true).text('Удаление…');
        try {
            const response = await fetch(url, {method: 'DELETE'});
            if (!response.ok || response.redirected) throw new Error('delete failed');
            const destination = new URL(await response.text(), window.location.origin);
            if (destination.origin !== window.location.origin) throw new Error('invalid redirect');
            window.location.assign(destination);
        } catch (_) {
            $('#delete-error').prop('hidden', false).text('Не удалось удалить записи. Обновите страницу и повторите попытку.');
            button.prop('disabled', false).text('Удалить');
        }
    });

    const calendarLocale = {
        firstDayOfWeek: 1,
        weekdays: {
            shorthand: ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'],
            longhand: ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
        },
        months: {
            shorthand: ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'],
            longhand: ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
        },
        rangeSeparator: ' — ', weekAbbreviation: 'Нед.', scrollTitle: 'Прокрутите для увеличения',
        toggleTitle: 'Нажмите для переключения', yearAriaLabel: 'Год', monthAriaLabel: 'Месяц',
        hourAriaLabel: 'Часы', minuteAriaLabel: 'Минуты', time_24hr: true
    };
    $('[data-role="datepicker"], [data-role="datetimepicker"]').not('[readonly]').each(function () {
        const withTime = this.dataset.role === 'datetimepicker';
        flatpickr(this, {
            enableTime: withTime, enableSeconds: withTime, allowInput: true, time_24hr: true,
            dateFormat: withTime ? 'Y-m-d H:i:s' : 'Y-m-d', locale: calendarLocale
        });
    });
});
