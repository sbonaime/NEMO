BadgeReader = function (on_success) {

    $(window).keypress(on_keypress).keydown(on_keydown);
    var record_badge_number = false;
    var badge_number = "";

    // Note that keydown and keyup provide a code indicating which key is pressed, while keypress indicates which character was entered. For example, a lowercase "a" will be reported as 65 by keydown and keyup, but as 97 by keypress. An uppercase "A" is reported as 65 by all events. Because of this distinction, when catching special keystrokes such as F2, .keydown() or .keyup() is a better choice. #}

    function on_keypress(event) {
        if (record_badge_number) {
            badge_number += String.fromCharCode(event.which);
            $("#badge_number").html(badge_number);
        }
    }

    function on_keydown(event) {
        if (event.which === 113) {// The F2 key activates badge number recording #}
            record_badge_number = !record_badge_number;
            if (!record_badge_number) {
                on_success(badge_number);
                $("#badge_number").html(badge_number + ", sent");
                badge_number = "";
            }
        }
    }
};