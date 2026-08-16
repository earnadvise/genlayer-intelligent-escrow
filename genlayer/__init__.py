import sys

# Define base contract class
class Contract:
    pass

# Storage decorator
def allow_storage(cls):
    return cls

# Mock Address Type
class Address:
    def __init__(self, val):
        if isinstance(val, Address):
            self.val = val.val
        else:
            self.val = str(val)
            
    @property
    def as_hex(self):
        return self.val
        
    def __eq__(self, other):
        if isinstance(other, Address):
            return self.val == other.val
        return self.val == str(other)
        
    def __str__(self):
        return self.val
        
    def __repr__(self):
        return f"Address('{self.val}')"
        
    def __hash__(self):
        return hash(self.val)

# Mock u256 Type
class u256(int):
    pass

# Mock TreeMap Type
class TreeMap(dict):
    def has(self, key):
        return key in self

# Global Mock VM state context
mock_vm_context = {
    'sender': Address("0x1"),
    'value': u256(0),
    'web_mocks': [],  # list of (regex_pattern, response_dict)
    'llm_mocks': [],  # list of (regex_pattern, response_string)
    'transfers': []   # list of (recipient_address, amount_u256)
}

# Mock gl.message
class MessageMock:
    @property
    def sender_address(self):
        return mock_vm_context['sender']
    @property
    def value(self):
        return mock_vm_context['value']

# Mock gl.vm
class VmMock:
    class UserError(Exception):
        pass

# Mock gl.nondet.web
class WebMock:
    def render(self, url, mode="text"):
        import re
        for pattern, resp in mock_vm_context['web_mocks']:
            if re.search(pattern, url, re.DOTALL):
                return resp["body"]
        return f"Mocked webpage content for {url}"

# Mock gl.nondet
class NondetMock:
    def __init__(self):
        self.web = WebMock()
        
    def exec_prompt(self, task, response_format="json"):
        import re
        import json
        for pattern, resp_str in mock_vm_context['llm_mocks']:
            if re.search(pattern, task, re.DOTALL):
                return json.loads(resp_str)
        # Default fallback mock response
        return {"payment_to_seller_percentage": 50, "reasoning": "Arbitration fallback mock"}

# Mock gl.eq_principle
class EqPrincipleMock:
    def strict_eq(self, func):
        # Directly executes the non-deterministic block for testing
        return func()

# Mock gl.evm
class EvmMock:
    def contract_interface(self, cls):
        class Wrapper:
            def __init__(self, address):
                self.address = address
            def emit_transfer(self, value, on='finalized'):
                mock_vm_context['transfers'].append((self.address, value))
        return Wrapper

# Mock gl.public decorator namespace
class PublicMock:
    def view(self, func):
        return func

    class WriteMock:
        def __call__(self, func):
            return func
        def payable(self, func):
            return func

    def __init__(self):
        self.write = self.WriteMock()

# Main gl namespace container
class GlMock:
    def __init__(self):
        self.message = MessageMock()
        self.vm = VmMock()
        self.nondet = NondetMock()
        self.eq_principle = EqPrincipleMock()
        self.evm = EvmMock()
        self.public = PublicMock()
        self.Contract = Contract

    def get_contract_at(self, address):
        # Returns a callable address object representing the interface
        return Address(address)

# Export gl object
gl = GlMock()
