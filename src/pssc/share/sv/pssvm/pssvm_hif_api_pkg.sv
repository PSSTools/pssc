`include "pyhdl_if_macros.svh"
package pssvm_hif_api_pkg;
    import pyhdl_if::*;
    interface class PssvmEngine_exp_if;
        pure virtual function int load(
            input string path,
            input string root_type,
            input string platform
        );

        pure virtual function int fresh();
        pure virtual function void push_arg(input longint unsigned v);
        pure virtual function int call_start(
            input string entry,
            input string comp_path
        );

        pure virtual function int run(input int h);
        pure virtual function int req_int(
            input int h,
            input string field
        );

        pure virtual function longint unsigned req_u64(
            input int h,
            input string field
        );

        pure virtual function string req_str(
            input int h,
            input string field
        );

        pure virtual function int req_nargs(input int h);
        pure virtual function longint unsigned req_arg(
            input int h,
            input int i
        );

        pure virtual function int complete(
            input int h,
            input longint unsigned data
        );

        pure virtual function longint unsigned result(input int h);
        pure virtual function void call_end(input int h);
        pure virtual function int notify(
            input int kind,
            input int id,
            input longint unsigned value
        );

        pure virtual function string error(input int h);
        pure virtual function string test_name(input int i);
        pure virtual function string test_expect(input int i);
        pure virtual function string hook(
            input string kind,
            input int i
        );

        pure virtual function int checks_failed();
        pure virtual function string api_check(input string spec);
        pure virtual function string serve(
            input string name,
            input string top
        );

        pure virtual function string trace(
            input string directory,
            input string name
        );

        pure virtual function string cur_op(input int h);
        pure virtual function string cur_file(input int h);
        pure virtual function int cur_line(input int h);
        pure virtual function void set_time(input longint unsigned t_fs);
        pure virtual function int poll(input int timeout_ms);
        pure virtual function int dbg_wait(input string why);
        pure virtual function string pending_sel();
        pure virtual function void serve_test(
            input string name,
            input int passed
        );

        pure virtual function void serve_done(
            input int n_pass,
            input int n_fail
        );

    endclass

    interface class PssvmEngine_imp_if;
    endclass

    class PssvmEngine_exp_impl implements PssvmEngine_exp_if;
        pyhdl_if::PyObject m_obj;
        function new(pyhdl_if::PyObject obj=null, bit create=1, string clsname="PssvmEngine");
            m_obj = obj;
            if (create && (m_obj == null)) begin
                m_obj = create_pyobj();
                if (m_obj != null) begin
                    pyhdl_if::pyhdl_if_connectObject(m_obj, null);
                end
            end else begin
                m_obj = obj;
            end
        endfunction

        static function pyhdl_if::PyObject create_pyobj(string modname="pssvm_dpi_shim", string clsname="PssvmEngine");
            pyhdl_if::PyObject __args, __cls_m, __cls_t, __obj;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            __args = pyhdl_if::PyTuple_New(0);
            __cls_m = pyhdl_if::PyImport_ImportModule(modname);

            if (__cls_m == null) begin
                pyhdl_if::PyErr_Print();
                $display("Fatal Error: Failed to find module %%s", modname);
                $finish;
                pyhdl_if::PyGILState_Release(state);
                return null;
            end

            __cls_t = pyhdl_if::PyObject_GetAttrString(__cls_m, clsname);
            if (__cls_t == null) begin
                pyhdl_if::PyErr_Print();
                $display("Fatal Error: Failed to find class %%s", clsname);
                $finish;
                pyhdl_if::PyGILState_Release(state);
                return null;
            end

            __obj = pyhdl_if::PyObject_Call(__cls_t, __args, null);
            if (__obj == null) begin
                pyhdl_if::PyErr_Print();
                $display("Fatal Error: Failed to construct class PssvmEngine");
                $finish;
                pyhdl_if::PyGILState_Release(state);
                return null;
            end

            pyhdl_if::PyGILState_Release(state);

            return __obj;
        endfunction


        virtual function int load(input string path, input string root_type, input string platform);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(3);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(path)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(root_type)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 2, pyhdl_if::PyUnicode_FromString(platform)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "load", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int fresh();
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(0);
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "fresh", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function void push_arg(input longint unsigned v);
            pyhdl_if::PyObject __res;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromUnsignedLongLong(v)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "push_arg", __args);
            pyhdl_if::Py_DecRef(__res);
            pyhdl_if::PyGILState_Release(state);
        endfunction
        virtual function int call_start(input string entry, input string comp_path);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(entry)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(comp_path)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "call_start", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int run(input int h);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "run", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int req_int(input int h, input string field);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(field)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "req_int", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function longint unsigned req_u64(input int h, input string field);
            pyhdl_if::PyObject __res;
            longint unsigned __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(field)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "req_u64", __args);
            __ret = 64'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string req_str(input int h, input string field);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(field)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "req_str", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int req_nargs(input int h);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "req_nargs", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function longint unsigned req_arg(input int h, input int i);
            pyhdl_if::PyObject __res;
            longint unsigned __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyLong_FromLong(longint'(i))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "req_arg", __args);
            __ret = 64'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int complete(input int h, input longint unsigned data);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyLong_FromUnsignedLongLong(data)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "complete", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function longint unsigned result(input int h);
            pyhdl_if::PyObject __res;
            longint unsigned __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "result", __args);
            __ret = 64'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function void call_end(input int h);
            pyhdl_if::PyObject __res;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "call_end", __args);
            pyhdl_if::Py_DecRef(__res);
            pyhdl_if::PyGILState_Release(state);
        endfunction
        virtual function int notify(input int kind, input int id, input longint unsigned value);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(3);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(kind))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyLong_FromLong(longint'(id))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 2, pyhdl_if::PyLong_FromUnsignedLongLong(value)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "notify", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string error(input int h);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "error", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string test_name(input int i);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(i))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "test_name", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string test_expect(input int i);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(i))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "test_expect", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string hook(input string kind, input int i);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(kind)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyLong_FromLong(longint'(i))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "hook", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int checks_failed();
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(0);
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "checks_failed", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string api_check(input string spec);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(spec)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "api_check", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string serve(input string name, input string top);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(name)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(top)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "serve", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string trace(input string directory, input string name);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(directory)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyUnicode_FromString(name)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "trace", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string cur_op(input int h);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "cur_op", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string cur_file(input int h);
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "cur_file", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int cur_line(input int h);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(h))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "cur_line", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function void set_time(input longint unsigned t_fs);
            pyhdl_if::PyObject __res;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromUnsignedLongLong(t_fs)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "set_time", __args);
            pyhdl_if::Py_DecRef(__res);
            pyhdl_if::PyGILState_Release(state);
        endfunction
        virtual function int poll(input int timeout_ms);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(timeout_ms))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "poll", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function int dbg_wait(input string why);
            pyhdl_if::PyObject __res;
            int __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(1);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(why)));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "dbg_wait", __args);
            __ret = int'(pyhdl_if::PyLong_AsLong(__res));
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function string pending_sel();
            pyhdl_if::PyObject __res;
            string __ret;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(0);
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "pending_sel", __args);
            __ret = pyhdl_if::PyUnicode_AsUTF8(__res);
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction
        virtual function void serve_test(input string name, input int passed);
            pyhdl_if::PyObject __res;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyUnicode_FromString(name)));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyLong_FromLong(longint'(passed))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "serve_test", __args);
            pyhdl_if::Py_DecRef(__res);
            pyhdl_if::PyGILState_Release(state);
        endfunction
        virtual function void serve_done(input int n_pass, input int n_fail);
            pyhdl_if::PyObject __res;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            pyhdl_if::PyObject __args = pyhdl_if::PyTuple_New(2);
            void'(pyhdl_if::PyTuple_SetItem(__args, 0, pyhdl_if::PyLong_FromLong(longint'(n_pass))));
            void'(pyhdl_if::PyTuple_SetItem(__args, 1, pyhdl_if::PyLong_FromLong(longint'(n_fail))));
            __res = pyhdl_if::pyhdl_if_invokePyFunc(m_obj, "serve_done", __args);
            pyhdl_if::Py_DecRef(__res);
            pyhdl_if::PyGILState_Release(state);
        endfunction

        function void callpy();
            // Prepares arguments and calls Python object
        endfunction
    endclass

    class PssvmEngine_imp_impl #(type ImpT=PssvmEngine_imp_if) implements pyhdl_if::ICallApi;
        ImpT m_impl;
        PyObject m_obj;
        function new(ImpT impl, pyhdl_if::PyObject obj=null, bit create=1, string clsname="PssvmEngine");
            m_impl = impl;
            if (obj == null && create) begin
                // Create an instance of the Python class
                m_obj = create_pyobj();
            end else begin
                m_obj = obj;
            end
            if (m_obj != null && m_impl != null) begin
                pyhdl_if::pyhdl_if_connectObject(m_obj, this);
            end
        endfunction

        static function pyhdl_if::PyObject create_pyobj(string modname="pssvm_dpi_shim", string clsname="PssvmEngine");
            pyhdl_if::PyObject __args, __cls_m, __cls_t, __obj;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            __args = pyhdl_if::PyTuple_New(0);
            __cls_m = pyhdl_if::PyImport_ImportModule(modname);

            if (__cls_m == null) begin
                pyhdl_if::PyErr_Print();
                $display("Fatal Error: Failed to find module %%s", modname);
                $finish;
                pyhdl_if::PyGILState_Release(state);
                return null;
            end

            __cls_t = pyhdl_if::PyObject_GetAttrString(__cls_m, clsname);
            if (__cls_t == null) begin
                pyhdl_if::PyErr_Print();
                $display("Fatal Error: Failed to find class %%s", clsname);
                $finish;
                pyhdl_if::PyGILState_Release(state);
                return null;
            end

            __obj = pyhdl_if::PyObject_Call(__cls_t, __args, null);
            if (__obj == null) begin
                pyhdl_if::PyErr_Print();
                $display("Fatal Error: Failed to construct class PssvmEngine");
                $finish;
                pyhdl_if::PyGILState_Release(state);
                return null;
            end

            pyhdl_if::PyGILState_Release(state);

            return __obj;
        endfunction


        virtual function pyhdl_if::PyObject invokeFunc(string method, pyhdl_if::PyObject args);
            pyhdl_if::PyObject __ret = pyhdl_if::None;
            pyhdl_if::PyGILState_STATE state = pyhdl_if::PyGILState_Ensure();
            case (method)
                default: begin
                    $display("Fatal: unsupported method call %0s", method);
                end
            endcase
            pyhdl_if::PyGILState_Release(state);
            return __ret;
        endfunction

        virtual task invokeTask(
            output pyhdl_if::PyObject retval,
            inout pyhdl_if::PyGILState_STATE state,
            input string method,
            input pyhdl_if::PyObject args);
            retval = pyhdl_if::None;
            case (method)
                default: begin
                    $display("Fatal: unsupported method call %0s", method);
                end
            endcase
        endtask
    endclass

endpackage
