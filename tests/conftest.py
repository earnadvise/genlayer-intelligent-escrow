import pytest
import sys
import os

# Insert the local intelligent-contracts folder onto Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from genlayer import mock_vm_context, Address, u256, TreeMap


@pytest.fixture
def direct_vm():
    """
    Simulates the direct VM context cheatcode environment.
    """

    class DirectVM:
        @property
        def sender(self):
            return mock_vm_context["sender"]

        @sender.setter
        def sender(self, val):
            mock_vm_context["sender"] = Address(val)

        @property
        def value(self):
            return mock_vm_context["value"]

        @value.setter
        def value(self, val):
            mock_vm_context["value"] = u256(val)

        def mock_web(self, pattern, response):
            mock_vm_context["web_mocks"].append((pattern, response))

        def mock_llm(self, pattern, response):
            mock_vm_context["llm_mocks"].append((pattern, response))

        def clear(self):
            mock_vm_context["sender"] = Address("0x1")
            mock_vm_context["value"] = u256(0)
            mock_vm_context["web_mocks"] = []
            mock_vm_context["llm_mocks"] = []
            mock_vm_context["transfers"] = []

    vm = DirectVM()
    vm.clear()
    return vm


@pytest.fixture
def direct_deploy():
    """
    Simulates direct in-memory deployment of the Intelligent Contract.
    It automatically binds mock storage containers (like TreeMap) based on type annotations.
    """

    def deploy(contract_path, *args, **kwargs):
        import importlib.util

        module_name = os.path.basename(contract_path).replace(".py", "")
        abs_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", contract_path)
        )

        spec = importlib.util.spec_from_file_location(module_name, abs_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Retrieve the main class
        contract_class = getattr(module, "ArbitratedEscrow")
        instance = contract_class(*args, **kwargs)

        # Automatically bind empty mock collections for storage annotations
        annotations = getattr(contract_class, "__annotations__", {})
        for attr, attr_type in annotations.items():
            if not hasattr(instance, attr):
                type_str = str(attr_type)
                if "TreeMap" in type_str:
                    setattr(instance, attr, TreeMap())
                elif "DynArray" in type_str:
                    setattr(instance, attr, [])
                else:
                    setattr(instance, attr, None)

        return instance

    return deploy
