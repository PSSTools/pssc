// The UVM component-name check, reimplemented without a regex.
//
// WHY THIS EXISTS. UVM's own check (uvm_traversal.svh,
// uvm_component_name_check_visitor) matches every component name against a
// POSIX regex:
//
//   /^[][[:alnum:](){}_:-]([][[:alnum:](){} _:-]*[][[:alnum:](){}_:-])?$/
//
// and `uvm_is_match` can only evaluate one of those through DPI. The UVM
// bundled with this Verilator is built with UVM_NO_DPI, which it says on every
// run:
//
//   [UVM/COMP/NAMECHECK] This implementation of the component name checks
//                        requires DPI to be enabled
//
// and the no-DPI `uvm_re_match` is documented in uvm/src/dpi/uvm_regex.svh as
// "does not match regular expressions, it only does glob style matching". So
// the bracket expression is compared LITERALLY, character by character, and no
// legal name can match it. Every component was reported -- "env", "sb", and
// UVM's own "uvm_test_top" among them. That was 580 of the regression's 580
// warnings: a noise floor deep enough that a real warning appearing would not
// have moved a number anyone reads.
//
// The fix is not to silence the ID. A silenced check and a working one look
// the same from the outside, and this one is worth having: a "." in a
// component name makes get_full_name() ambiguous, which is exactly the sort of
// thing that is discovered late. So the ruleset is applied directly instead --
// the same one stated in the comment above the UVM class:
//
//   * allowed characters are alphanumerics plus "(){}[]_:-"
//   * a space is allowed, but not as the first or last character
//   * the hierarchy delimiter "." is never allowed
//
// Installed by wb_dma_base_test::build_phase() via
// uvm_coreservice_t::set_component_visitor(), which is the documented seam;
// uvm_root runs the visitor at end_of_elaboration, so build time is early
// enough.
class wb_dma_name_check_visitor extends uvm_component_name_check_visitor;

    function new(string name = "name-check-visitor");
        super.new(name);
    endfunction

    // Legal anywhere in a name. Space is excluded here and handled by the
    // caller, because it is legal only in the interior.
    local function bit legal_char(byte c);
        return (c >= "a" && c <= "z") ||
               (c >= "A" && c <= "Z") ||
               (c >= "0" && c <= "9") ||
               c == "_" || c == "-" || c == ":" ||
               c == "(" || c == ")" || c == "{" || c == "}" ||
               c == "[" || c == "]";
    endfunction

    // The base class's version announces that the check needs DPI. It does not
    // any more, so the note would be misleading. It also caches the root, which
    // this implementation looks up itself.
    virtual function void begin_v();
    endfunction

    virtual function void visit(uvm_component node);
        uvm_coreservice_t cs = uvm_coreservice_t::get();
        string s = node.get_name();

        if (node == cs.get_root()) return;      // the root is exempt, as in UVM

        if (s.len() == 0) begin
            `uvm_warning("UVM/COMP/NAME",
                $sformatf("the component \"%s\" has an empty name",
                          node.get_full_name()))
            return;
        end

        for (int i = 0; i < s.len(); i++) begin
            byte c = s[i];
            // A space is legal in the interior only -- a leading or trailing
            // one is invisible in a log and makes two names look identical.
            bit ok = legal_char(c) ||
                     (c == " " && i != 0 && i != s.len()-1);
            if (!ok) begin
                `uvm_warning("UVM/COMP/NAME",
                    $sformatf("the name \"%s\" of the component \"%s\" violates the uvm component name constraints (illegal '%s' at position %0d)",
                              s, node.get_full_name(), string'(c), i))
                return;                          // one report per component
            end
        end
    endfunction
endclass
