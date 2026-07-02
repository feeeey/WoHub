def test_validator_protocol_shape():
    from agent.validator import StrategyValidator, ValidationReport, NullValidator
    v: StrategyValidator = NullValidator()
    report = v.validate({"name": "x", "rules": []})
    assert isinstance(report, ValidationReport)
    assert report.verdict == "not_validated"
